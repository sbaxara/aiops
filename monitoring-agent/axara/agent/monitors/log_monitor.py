# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Log Monitor
Monitors application log files for errors and exceptions
"""
import re
import glob
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from .base_monitor import BaseMonitor
import logging

logger = logging.getLogger(__name__)

__version__ = "1.4.0"


class LogMonitor(BaseMonitor):
    """
    Monitor application log files
    Searches for error patterns within time window (check_interval_seconds)
    """

    UNIT = "none"
    
    def check(self) -> Dict[str, Any]:
        """
        Check log files for error patterns
        Uses check_interval_seconds as time window: (current_time - check_interval_seconds) to current_time
        
        Returns:
            Result dict with log analysis
        """
        try:
            config = self.config.get('config', {})
            
            # Time window = check_interval_seconds seconds
            cutoff_time = datetime.now() - timedelta(seconds=self.check_interval_seconds)
            
            # Get configuration
            log_paths = config.get('log_paths', [])
            patterns = config.get('patterns', [])
            context_lines = config.get('include_context_lines', 5)
            
            if not log_paths or not patterns:
                return self.create_result(
                    name='log_check',
                    value=0,
                    status='error',
                    details={'error': 'Missing log_paths or patterns in configuration'}
                )
            
            # Results structure
            results = {
                'status': 'pass',
                'details': {
                    'files_checked': [],
                    'patterns': {}
                }
            }
            
            # Initialize pattern tracking
            for pattern_config in patterns:
                pattern_name = pattern_config['name']
                results['details']['patterns'][pattern_name] = {
                    'count': 0,
                    'threshold': pattern_config.get('threshold', 0),
                    'matches': []
                }
            
            # Process each log path pattern (supports glob)
            for log_path_pattern in log_paths:
                log_files = glob.glob(log_path_pattern)
                
                if not log_files:
                    logger.warning(f"No log files found matching pattern: {log_path_pattern}")
                    continue
                
                for log_file in log_files:
                    file_results = self._check_log_file(
                        log_file,
                        patterns,
                        cutoff_time,
                        context_lines
                    )
                    
                    results['details']['files_checked'].append({
                        'file': log_file,
                        'patterns': file_results
                    })
                    
                    # Aggregate pattern counts across all files
                    for pattern_name, pattern_result in file_results.items():
                        results['details']['patterns'][pattern_name]['count'] += pattern_result['count']
                        results['details']['patterns'][pattern_name]['matches'].extend(
                            pattern_result['matches']
                        )
            
            # Evaluate thresholds for each pattern at aggregate level
            for pattern_name, pattern_result in results['details']['patterns'].items():
                if pattern_result['count'] > pattern_result['threshold']:
                    results['status'] = 'fail'
                    pattern_result['threshold_breached'] = True
                else:
                    pattern_result['threshold_breached'] = False

            # Propagate threshold_breached back into each file_result's pattern
            # entry. The state machine reads threshold_breached from the per-file
            # pattern dict (files_checked[n]['patterns'][name]) — without this,
            # it always sees False and never increments consecutive_failures,
            # so no incident is ever created even when the threshold is breached.
            agg_patterns = results['details']['patterns']
            for file_entry in results['details']['files_checked']:
                for pat_name, pat_data in file_entry.get('patterns', {}).items():
                    if pat_name in agg_patterns:
                        pat_data['threshold_breached'] = agg_patterns[pat_name]['threshold_breached']
            
            consec = self.config.get('consecutive_failures', 3)
            failure_reasons = []
            for pattern_name, pattern_result in results['details']['patterns'].items():
                if pattern_result.get('threshold_breached'):
                    count     = pattern_result['count']
                    threshold = pattern_result['threshold']
                    # Find which log files triggered this pattern
                    files_hit = [
                        f['file']
                        for f in results['details']['files_checked']
                        if pattern_name in f.get('patterns', {})
                        and f['patterns'][pattern_name].get('count', 0) > 0
                    ]
                    files_str = ', '.join(files_hit) if files_hit else 'log files'
                    failure_reasons.append(
                        f"Pattern '{pattern_name}' in {self.name} matched {count} times "
                        f"(threshold: {threshold}) consecutively {consec} in {files_str}"
                    )

            if failure_reasons:
                results['details']['failure_reason'] = "; ".join(failure_reasons)

            # Calculate total match count from aggregate before removing it
            total_count = sum(p['count'] for p in results['details']['patterns'].values())

            # Remove the top-level 'patterns' aggregate from details before
            # sending — all pattern data (including threshold_breached) is
            # already present in files_checked[n]['patterns'] after propagation.
            # Keeping it would duplicate every pattern in the payload.
            details_out = {
                k: v for k, v in results['details'].items() if k != 'patterns'
            }

            return self.create_result(
                name='log_check',
                value=total_count,
                status=results['status'],
                details=details_out
            )
            
        except Exception as e:
            return self.handle_error(e)
    
    def _check_log_file(
        self,
        log_file: str,
        patterns: List[Dict],
        cutoff_time: datetime,
        context_lines: int
    ) -> Dict[str, Any]:
        """
        Check single log file for all patterns
        
        Args:
            log_file: Path to log file
            patterns: List of pattern configs
            cutoff_time: Only process logs after this time
            context_lines: Number of lines before/after match to include
            
        Returns:
            Dict with pattern results for this file
        """
        pattern_results = {}
        
        # Initialize results for each pattern
        for pattern_config in patterns:
            pattern_results[pattern_config['name']] = {
                'count': 0,
                'threshold': pattern_config.get('threshold', 0),
                'matches': []
            }
        
        try:
            # Efficient tail-read: only load lines from the end of the file
            # that fall within the cutoff window, avoiding a full read of
            # large log files (e.g. 60k+ lines) on every check cycle.
            lines = self._read_recent_lines(log_file, cutoff_time)
            if not lines:
                return pattern_results

            # Pre-compute effective timestamps with carry-forward so
            # continuation/stack-trace lines inherit the preceding line's ts.
            line_timestamps: List[Optional[datetime]] = []
            last_known_ts: Optional[datetime] = None
            for line in lines:
                ts = self._extract_timestamp(line)
                if ts is not None:
                    last_known_ts = ts
                line_timestamps.append(last_known_ts)

            # In-window indices: only lines with a confirmed recent timestamp.
            in_window: List[int] = [
                i for i, ts in enumerate(line_timestamps)
                if ts is not None and ts >= cutoff_time
            ]

            # First-match-wins per line: iterate lines in the window, check
            # each pattern in declaration order, record the first match only.
            # This prevents a single line matching multiple patterns (e.g. a
            # line containing both "error" and "warning") from being counted
            # multiple times across different pattern buckets.
            # Consecutive matching lines are grouped into runs so a full stack
            # trace counts as one error event, not one per line.

            # Build per-pattern match index using first-match-wins
            claimed: set = set()           # line indices already claimed by a pattern
            for pattern_config in patterns:
                pattern_name  = pattern_config['name']
                match_indices = [
                    i for i in in_window
                    if i not in claimed
                    and self._matches_pattern(lines[i], pattern_config)
                ]
                for i in match_indices:
                    claimed.add(i)         # mark so later patterns skip this line

                if not match_indices:
                    continue

                # Group consecutive indices into runs (one run = one event)
                runs: List[List[int]] = []
                run = [match_indices[0]]
                for idx in match_indices[1:]:
                    if idx == run[-1] + 1:
                        run.append(idx)
                    else:
                        runs.append(run)
                        run = [idx]
                runs.append(run)

                for run in runs:
                    # One event per run — not one per line in the run.
                    pattern_results[pattern_name]['count'] += 1

                    # Only record the first match as context — the count
                    # already tells the operator how many times it fired.
                    # Recording all 74 matches bloats the payload and the
                    # incident description with duplicate context blocks.
                    # Cap matches per pattern per file (payload safety, D2 4.4).
                    if len(pattern_results[pattern_name]['matches']) < 50:
                        first_i, last_i = run[0], run[-1]
                        # Structured context: before the matched line, and after
                        # (the after-window carries stack traces), each capped to
                        # include_context_lines. Each line length-capped to 2000.
                        def _cap(s):
                            """Strip trailing whitespace and cut a line to 2000 characters."""
                            return s.rstrip()[:2000]
                        ctx_before_start = max(0, first_i - context_lines)
                        context_before = [_cap(lines[i]) for i in range(ctx_before_start, first_i)]
                        ctx_after_end = min(len(lines), last_i + context_lines + 1)
                        context_after = [_cap(lines[i]) for i in range(last_i + 1, ctx_after_end)]
                        effective_ts = line_timestamps[first_i]
                        pattern_results[pattern_name]['matches'].append({
                            'line_number':    first_i + 1,
                            'timestamp':      effective_ts.isoformat() if effective_ts else None,
                            'level':          pattern_config['level'],
                            'match_count':    len(run),
                            'matched_text':   _cap(lines[first_i]),
                            'context_before': context_before,
                            'context_after':  context_after,
                        })

        except Exception as e:
            logger.error(f"Error reading log file {log_file}: {e}")
        
        return pattern_results
    
    def _matches_pattern(self, line: str, pattern_config: Dict) -> bool:
        """
        Check if line matches pattern
        
        Args:
            line: Log line to check
            pattern_config: Pattern configuration
            
        Returns:
            True if line matches
        """
        regex = pattern_config.get('regex', '')
        if not regex:
            return False
        
        try:
            return bool(re.search(regex, line, re.IGNORECASE))
        except re.error as e:
            logger.error(f"Invalid regex pattern '{regex}': {e}")
            return False
    
    def _extract_timestamp(self, line: str) -> Optional[datetime]:
        """
        Extract timestamp from log line
        Supports multiple common log formats
        
        Args:
            line: Log line
            
        Returns:
            datetime object or None
        """
        # Common timestamp patterns — most-specific first.
        # ISO patterns consume (but do not capture) any trailing timezone offset
        # (+11, +11:00, -05:30, Z) so strptime always receives a naive datetime
        # that compares correctly with datetime.now().
        patterns = [
            # ISO T + optional offset: 2026-03-03T10:15:23+11:00
            (r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:[+-]\d{2}:?\d{2}|Z)?',
             '%Y-%m-%dT%H:%M:%S'),
            # ISO space + optional offset: 2026-03-03 10:15:23+11
            (r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.\d+)?(?:\s+[A-Z]{2,5}|[+-]\d{2}:?\d{0,2}|Z)?',
             '%Y-%m-%d %H:%M:%S'),
            # Log4j: 2026-03-03 10:15:23,456
            (r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}),\d+',
             '%Y-%m-%d %H:%M:%S'),
            # Apache/nginx: 03/Mar/2026:10:15:23
            (r'(\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2})',
             '%d/%b/%Y:%H:%M:%S'),
            # macOS: Tue Mar 24 01:07:34 or Tue Mar 24 01:07:34.158
            (r'\w{3}\s+(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})(?:\.\d+)?',
             '%b %d %H:%M:%S'),
            # Syslog: Mar  3 10:15:23
            (r'(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})',
             '%b %d %H:%M:%S'),
        ]
        
        for regex_pattern, date_format in patterns:
            match = re.search(regex_pattern, line)
            if match:
                try:
                    timestamp_str = match.group(1)
                    # For syslog format, add current year
                    if '%b %d %H:%M:%S' in date_format:
                        timestamp_str = f"{timestamp_str} {datetime.now().year}"
                        date_format = '%b %d %H:%M:%S %Y'
                    
                    return datetime.strptime(timestamp_str, date_format)
                except ValueError:
                    continue
        
        return None
    
    def _read_recent_lines(
        self,
        log_file: str,
        cutoff_time: datetime,
        chunk_size: int = 65536,
    ) -> List[str]:
        """
        Read only the tail of log_file likely to contain lines within the
        cutoff window. Walks backwards in chunks until a line whose timestamp
        predates cutoff_time is found, then returns everything from that point
        forward. Falls back to a full read on any I/O error.
        """
        import os
        try:
            file_size = os.path.getsize(log_file)
            if file_size == 0:
                return []

            with open(log_file, 'rb') as f:
                pos        = file_size
                leftover   = b''
                cutoff_pos = 0

                while pos > 0:
                    read_size = min(chunk_size, pos)
                    pos      -= read_size
                    f.seek(pos)
                    chunk     = f.read(read_size) + leftover
                    raw_lines = chunk.split(b'\n')
                    leftover  = raw_lines[0]

                    found_old = False
                    for lb in reversed(raw_lines[1:]):
                        line = lb.decode('utf-8', errors='ignore')
                        ts   = self._extract_timestamp(line)
                        if ts is not None and ts < cutoff_time:
                            nl_pos = chunk.rfind(b'\n' + lb)
                            cutoff_pos = pos + (nl_pos if nl_pos >= 0 else 0)
                            found_old  = True
                            break
                    if found_old:
                        break

                f.seek(max(0, cutoff_pos))
                tail = f.read().decode('utf-8', errors='ignore')

            lines = tail.splitlines(keepends=True)
            if cutoff_pos > 0 and lines:
                lines = lines[1:]   # drop potentially partial first line
            return lines

        except Exception as e:
            logger.warning(
                f"Tail-read failed for {log_file}, falling back to full read: {e}"
            )
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.readlines()
            except Exception as e2:
                logger.error(f"Cannot read log file {log_file}: {e2}")
                return []
