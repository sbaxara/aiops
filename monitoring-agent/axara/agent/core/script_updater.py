# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Script Updater
Checks for and applies monitor script updates from central server
"""
import os
import hashlib
import shutil
from pathlib import Path
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


class ScriptUpdater:
    """
    Manages monitor script versions and updates
    """
    
    def __init__(self, agent):
        """
        Initialize script updater
        
        Args:
            agent: MonitoringAgent instance
        """
        self.agent = agent
        from . import monitor_loader
        self.scripts_dir = monitor_loader.monitors_dir()
        self.backup_dir = self.scripts_dir / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
    
    def check_and_update_scripts(self):
        """Check for script updates and apply them"""
        logger.info("Checking for monitor script updates...")
        
        local_scripts = self.scan_local_scripts()
        
        if not local_scripts:
            logger.info("No local scripts found to check")
            return False
        
        # Check with server
        update_info = self.agent.api_sender.check_script_versions(
            self.agent.server_id,
            local_scripts
        )
        
        if not update_info:
            logger.warning("Failed to check script versions with server")
            return False
        
        if not update_info.get('updates_available'):
            logger.info("All monitor scripts are up to date")
            return False
        
        # Apply updates
        scripts_to_update = update_info.get('scripts_to_update', [])
        if scripts_to_update:
            logger.info(f"Found {len(scripts_to_update)} script(s) to update")
            updated = self.apply_updates(scripts_to_update)
            if updated:
                logger.info(
                    "Monitor scripts updated — agent will restart monitoring loop "
                    "to reload new script code into memory"
                )
                return True
        return False
    
    def scan_local_scripts(self) -> List[Dict]:
        """
        Scan monitors/ directory for scripts
        
        Returns:
            List of script info dicts
        """
        scripts = []
        
        if not self.scripts_dir.exists():
            return scripts
        
        for file_path in self.scripts_dir.glob("*_monitor.py"):
            version, checksum = self.get_script_metadata(file_path)
            
            scripts.append({
                'script_name': file_path.name,
                'version': version,
                'checksum': checksum
            })
        
        return scripts
    
    def get_script_metadata(self, file_path: Path) -> tuple:
        """
        Extract version and calculate checksum
        
        Args:
            file_path: Path to script file
            
        Returns:
            Tuple of (version, checksum)
        """
        version = "unknown"
        
        # Extract version from __version__ variable
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('__version__'):
                    version = line.split('=')[1].strip().strip('"\'')
                    break

        # Calculate SHA256 checksum in binary mode so it matches the server's
        # checksum exactly on both Windows and Linux (no line-ending conversion).
        h = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)

        return version, f"sha256:{h.hexdigest()}"
    
    def apply_updates(self, updates: List[Dict]) -> bool:
        """
        Download and apply script updates.

        Returns:
            True if at least one script was successfully installed.
            The caller uses this to trigger a monitoring loop restart so
            the new code is imported fresh (Python caches modules in
            sys.modules — writing a new .py file does not reload the class
            already in memory).

        Args:
            updates: List of update info dicts
        """
        any_installed = False
        from .monitor_loader import SCRIPT_NAME
        for update in updates:
            script_name = update['script_name']
            current_version = update.get('current_version')
            latest_version = update['latest_version']
            if not SCRIPT_NAME.match(script_name or ''):
                logger.error(f"Refusing script with an invalid name: {script_name!r}")
                continue
            if current_version is None:
                logger.info(f"Installing new monitor script {script_name} {latest_version}")
            
            logger.info(
                f"Updating {script_name} from {current_version} to {latest_version}"
            )
            
            try:
                # Backup current version
                self.backup_script(script_name)
                
                # Download new version
                new_script_content = self.agent.api_sender.download_script(
                    script_name,
                    latest_version
                )
                
                if not new_script_content:
                    logger.error(f"Failed to download {script_name}")
                    self.restore_backup(script_name)
                    continue
                
                # Checksum is MANDATORY: an update without one is refused (the server
                # always sends it). Note this protects integrity, not authenticity —
                # authenticity of the server comes from TLS verification (ssl_verify
                # must be true or a CA path; see the hardening guide).
                expected_checksum = update.get('checksum')
                actual_checksum = f"sha256:{hashlib.sha256(new_script_content.encode('utf-8')).hexdigest()}"
                if not expected_checksum or actual_checksum != expected_checksum:
                    logger.error(
                        f"Refusing update for {script_name}: "
                        f"{'no checksum supplied' if not expected_checksum else 'checksum mismatch'}\n"
                        f"Expected: {expected_checksum}\n"
                        f"Got: {actual_checksum}"
                    )
                    self.restore_backup(script_name)
                    continue
                
                # Install new version
                self.install_script(script_name, new_script_content)
                logger.info(f"Successfully updated {script_name}")
                any_installed = True

            except Exception as e:
                logger.error(f"Error updating {script_name}: {e}")
                self.restore_backup(script_name)

        return any_installed
    
    def backup_script(self, script_name: str):
        """
        Backup current version of script
        
        Args:
            script_name: Script filename
        """
        source = self.scripts_dir / script_name
        
        if not source.exists():
            return
        
        # Create timestamped backup
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f"{script_name}.{timestamp}.bak"
        destination = self.backup_dir / backup_name
        
        shutil.copy2(source, destination)
        logger.debug(f"Backed up {script_name} to {destination}")
    
    def install_script(self, script_name: str, content: str):
        """
        Install new script version
        
        Args:
            script_name: Script filename
            content: Script content
        """
        destination = self.scripts_dir / script_name
        
        with open(destination, 'wb') as f:
            f.write(content.encode('utf-8'))
        
        logger.debug(f"Installed {script_name}")
    
    def restore_backup(self, script_name: str):
        """
        Restore most recent backup
        
        Args:
            script_name: Script filename
        """
        # Find most recent backup
        backups = list(self.backup_dir.glob(f"{script_name}.*.bak"))
        
        if not backups:
            logger.warning(f"No backup found for {script_name}")
            return
        
        most_recent = max(backups, key=lambda p: p.stat().st_mtime)
        destination = self.scripts_dir / script_name
        
        shutil.copy2(most_recent, destination)
        logger.info(f"Restored {script_name} from backup: {most_recent.name}")
