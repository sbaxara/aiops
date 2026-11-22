# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Database Monitor
Monitors database health (MongoDB, MySQL, PostgreSQL)
"""
import time
from typing import Dict, Any, Optional
from .base_monitor import BaseMonitor
import logging

logger = logging.getLogger(__name__)

__version__ = "1.2.0"


class DatabaseMonitor(BaseMonitor):
    """
    Monitor database health
    Supports MongoDB, MySQL, PostgreSQL
    """

    UNIT = "boolean"
    
    def check(self) -> Dict[str, Any]:
        """
        Check database health based on subtype
        
        Returns:
            Result dict with database metrics
        """
        try:
            config = self.config.get('config', {})
            subtype = self.config.get('subtype')  # mongodb, mysql, postgres
            
            if subtype == 'mongodb':
                return self._check_mongodb(config)
            elif subtype == 'mysql':
                return self._check_mysql(config)
            elif subtype == 'postgres':
                return self._check_postgres(config)
            else:
                return self.create_result(
                    name='db_health',
                    value=0,
                    status='error',
                    details={'error': f'Unknown database subtype: {subtype}'}
                )
                
        except Exception as e:
            return self.handle_error(e)
    
    def _check_mongodb(self, config: Dict) -> Dict[str, Any]:
        """Check MongoDB health"""
        try:
            from pymongo import MongoClient
            from pymongo.errors import ConnectionFailure, OperationFailure
                       
            host = config.get('host', 'localhost')
            port = config.get('port', 27017)
            user = config.get('user')
            password = config.get('password')
            db_name = config.get('database', 'admin')
            timeout_seconds = config.get('timeout_seconds', 10)

            if not user or not password:
                raise ValueError("Missing user or password in config")
            
            # Measure connection time
            start_time = time.time()
            client = MongoClient(host=host,
                                 port=port,
                                 username=user,
                                 password=password,
                                 authSource=db_name,
                                 serverSelectionTimeoutMS=timeout_seconds * 1000
            )
            
            # Ping to verify connection
            client.admin.command('ping')
            connection_time_ms = (time.time() - start_time) * 1000
            
            # Get server info
            server_info = client.server_info()
            
            # Get database stats
            db_stats = client[db_name].command('dbStats')
            
            # Get connection pool info
            server_status = client.admin.command('serverStatus')
            connections = server_status.get('connections', {})
            
            # Get replication info (if replica set)
            replication_info = {}
            try:
                repl_status = client.admin.command('replSetGetStatus')
                replication_info = {
                    'is_replica_set': True,
                    'state': repl_status.get('myState'),
                    'members': len(repl_status.get('members', []))
                }
                
                # Calculate replication lag
                if repl_status.get('members'):
                    primary_optime = None
                    secondary_optime = None
                    for member in repl_status['members']:
                        if member['stateStr'] == 'PRIMARY':
                            primary_optime = member.get('optimeDate')
                        elif member['stateStr'] == 'SECONDARY':
                            secondary_optime = member.get('optimeDate')
                    
                    if primary_optime and secondary_optime:
                        lag = (primary_optime - secondary_optime).total_seconds() * 1000
                        replication_info['replication_lag_ms'] = abs(lag)
                        
            except OperationFailure:
                replication_info['is_replica_set'] = False
            
            client.close()
            
            # Evaluate thresholds
            status = 'pass'
            threshold_config = self.threshold_config
            
            if threshold_config.get('connection_time_ms'):
                conn_time_status = self.evaluate_threshold(
                    connection_time_ms,
                    threshold_config['connection_time_ms']
                )
                if conn_time_status != 'pass':
                    status = conn_time_status
            
            if replication_info.get('replication_lag_ms'):
                if threshold_config.get('replication_lag_ms'):
                    lag_status = self.evaluate_threshold(
                        replication_info['replication_lag_ms'],
                        threshold_config['replication_lag_ms']
                    )
                    if lag_status == 'fail':
                        status = 'fail'
            
            consec = self.config.get('consecutive_failures', 3)
            ct_thresh = self.threshold_config.get('connection_time_ms', {})
            failure_reason = None
            if status != 'pass':
                if ct_thresh.get('critical') and connection_time_ms >= ct_thresh['critical']:
                    failure_reason = (
                        f"MongoDB connection time {connection_time_ms:.1f}ms in {self.name} "
                        f"exceeded consecutively {consec} times critical threshold of "
                        f"{ct_thresh['critical']}ms"
                    )
                elif ct_thresh.get('warning') and connection_time_ms >= ct_thresh['warning']:
                    failure_reason = (
                        f"MongoDB connection time {connection_time_ms:.1f}ms in {self.name} "
                        f"exceeded consecutively {consec} times warning threshold of "
                        f"{ct_thresh['warning']}ms"
                    )
                lag = replication_info.get('replication_lag_ms')
                lag_thresh = self.threshold_config.get('replication_lag_ms', {})
                if lag and lag_thresh.get('critical') and lag >= lag_thresh['critical']:
                    failure_reason = (failure_reason + "; " if failure_reason else "") + (
                        f"Replication lag {lag:.0f}ms exceeded consecutively {consec} times "
                        f"critical threshold {lag_thresh['critical']}ms"
                    )

            details = {
                'connection_time_ms': connection_time_ms,
                'version': server_info.get('version'),
                'active_connections': connections.get('current'),
                'available_connections': connections.get('available'),
                'database_size_mb': db_stats.get('dataSize', 0) / (1024 ** 2),
                'collection_count': db_stats.get('collections'),
                'replication': replication_info
            }
            if failure_reason:
                details['failure_reason'] = failure_reason

            return self.create_result(
                name='mongodb_health',
                value=1,
                status=status,
                details=details
            )
            
        except (ConnectionFailure, OperationFailure) as e:
            return self.create_result(
                name='mongodb_health',
                value=0,
                status='fail',
                details={'error': f'MongoDB connection failed: {str(e)}'}
            )
        except ImportError:
            return self.create_result(
                name='mongodb_health',
                value=0,
                status='error',
                details={'error': 'pymongo library not installed'}
            )
    
    def _check_mysql(self, config: Dict) -> Dict[str, Any]:
        """Check MySQL health"""
        try:
            import pymysql
            from pymysql import OperationalError
            
            host = config.get('host', 'localhost')
            port = config.get('port', 3306)
            user = config.get('user')
            password = config.get('password')
            database = config.get('database', 'mysql')
            timeout_seconds = config.get('timeout_seconds', 10)
            
            if not user or not password:
                raise ValueError("Missing user or password in config")
            
            # Measure connection time
            start_time = time.time()
            connection = pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                connect_timeout=timeout_seconds
            )
            connection_time_ms = (time.time() - start_time) * 1000
            
            cursor = connection.cursor()
            
            # Get version
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()[0]
            
            # Get connection count
            cursor.execute("SHOW STATUS LIKE 'Threads_connected'")
            threads_connected = int(cursor.fetchone()[1])
            
            cursor.execute("SHOW STATUS LIKE 'Max_used_connections'")
            max_used_connections = int(cursor.fetchone()[1])
            
            # Get database size
            cursor.execute(f"""
                SELECT 
                    SUM(data_length + index_length) / (1024 * 1024) AS size_mb
                FROM information_schema.TABLES
                WHERE table_schema = '{database}'
            """)
            size_result = cursor.fetchone()
            database_size_mb = float(size_result[0]) if size_result[0] else 0
            
            # Check replication status
            replication_info = {}
            try:
                cursor.execute("SHOW SLAVE STATUS")
                slave_status = cursor.fetchone()
                if slave_status:
                    replication_info = {
                        'is_slave': True,
                        'slave_io_running': slave_status[10] == 'Yes',
                        'slave_sql_running': slave_status[11] == 'Yes',
                        'seconds_behind_master': slave_status[32]
                    }
            except:
                replication_info['is_slave'] = False
            
            cursor.close()
            connection.close()
            
            # Evaluate thresholds
            status = 'pass'
            if self.threshold_config.get('connection_time_ms'):
                status = self.evaluate_threshold(
                    connection_time_ms,
                    self.threshold_config['connection_time_ms']
                )
            
            consec = self.config.get('consecutive_failures', 3)
            ct_thresh = self.threshold_config.get('connection_time_ms', {})
            failure_reason = None
            if status != 'pass':
                if ct_thresh.get('critical') and connection_time_ms >= ct_thresh['critical']:
                    failure_reason = (
                        f"MySQL connection time {connection_time_ms:.1f}ms in {self.name} "
                        f"exceeded consecutively {consec} times critical threshold of "
                        f"{ct_thresh['critical']}ms"
                    )
                elif ct_thresh.get('warning') and connection_time_ms >= ct_thresh['warning']:
                    failure_reason = (
                        f"MySQL connection time {connection_time_ms:.1f}ms in {self.name} "
                        f"exceeded consecutively {consec} times warning threshold of "
                        f"{ct_thresh['warning']}ms"
                    )

            details = {
                'connection_time_ms': connection_time_ms,
                'version': version,
                'threads_connected': threads_connected,
                'max_used_connections': max_used_connections,
                'database_size_mb': database_size_mb,
                'replication': replication_info
            }
            if failure_reason:
                details['failure_reason'] = failure_reason

            return self.create_result(
                name='mysql_health',
                value=1,
                status=status,
                details=details
            )
            
        except OperationalError as e:
            return self.create_result(
                name='mysql_health',
                value=0,
                status='fail',
                details={'error': f'MySQL connection failed: {str(e)}'}
            )
        except ImportError:
            return self.create_result(
                name='mysql_health',
                value=0,
                status='error',
                details={'error': 'pymysql library not installed'}
            )
    
    def _check_postgres(self, config: Dict) -> Dict[str, Any]:
        """Check PostgreSQL health"""
        try:
            import psycopg2
            from psycopg2 import OperationalError
            
            host = config.get('host', 'localhost')
            port = config.get('port', 5432)
            user = config.get('user')
            password = config.get('password')
            database = config.get('database', 'postgres')
            timeout_seconds = config.get('timeout_seconds', 10)
            
            if not user or not password:
                raise ValueError("Missing user or password in config")
            
            # Measure connection time
            start_time = time.time()
            connection = psycopg2.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                connect_timeout=timeout_seconds
            )
            connection_time_ms = (time.time() - start_time) * 1000
            
            cursor = connection.cursor()
            
            # Get version
            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]
            
            # Get connection count
            cursor.execute("""
                SELECT count(*) FROM pg_stat_activity 
                WHERE state = 'active'
            """)
            active_connections = cursor.fetchone()[0]
            
            cursor.execute("SELECT count(*) FROM pg_stat_activity")
            total_connections = cursor.fetchone()[0]
            
            # Get database size
            cursor.execute(f"""
                SELECT pg_database_size('{database}') / (1024 * 1024) AS size_mb
            """)
            database_size_mb = float(cursor.fetchone()[0])
            
            # Check replication status
            replication_info = {}
            try:
                cursor.execute("SELECT pg_is_in_recovery()")
                is_replica = cursor.fetchone()[0]
                replication_info['is_replica'] = is_replica
                
                if is_replica:
                    cursor.execute("""
                        SELECT 
                            EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp())) * 1000 
                        AS replication_lag_ms
                    """)
                    result = cursor.fetchone()
                    replication_info['replication_lag_ms'] = float(result[0]) if result[0] else 0
            except:
                replication_info['is_replica'] = False
            
            cursor.close()
            connection.close()
            
            # Evaluate thresholds
            status = 'pass'
            if self.threshold_config.get('connection_time_ms'):
                status = self.evaluate_threshold(
                    connection_time_ms,
                    self.threshold_config['connection_time_ms']
                )
            
            consec = self.config.get('consecutive_failures', 3)
            ct_thresh = self.threshold_config.get('connection_time_ms', {})
            failure_reason = None
            if status != 'pass':
                if ct_thresh.get('critical') and connection_time_ms >= ct_thresh['critical']:
                    failure_reason = (
                        f"PostgreSQL connection time {connection_time_ms:.1f}ms in {self.name} "
                        f"exceeded consecutively {consec} times critical threshold of "
                        f"{ct_thresh['critical']}ms"
                    )
                elif ct_thresh.get('warning') and connection_time_ms >= ct_thresh['warning']:
                    failure_reason = (
                        f"PostgreSQL connection time {connection_time_ms:.1f}ms in {self.name} "
                        f"exceeded consecutively {consec} times warning threshold of "
                        f"{ct_thresh['warning']}ms"
                    )
                repl_lag = replication_info.get('replication_lag_ms')
                repl_thresh = self.threshold_config.get('replication_lag_ms', {})
                if repl_lag and repl_thresh.get('critical') and repl_lag >= repl_thresh['critical']:
                    failure_reason = (failure_reason + "; " if failure_reason else "") + (
                        f"Replication lag {repl_lag:.0f}ms exceeded critical threshold "
                        f"{repl_thresh['critical']}ms"
                    )

            details = {
                'connection_time_ms': connection_time_ms,
                'version': version,
                'active_connections': active_connections,
                'total_connections': total_connections,
                'database_size_mb': database_size_mb,
                'replication': replication_info
            }
            if failure_reason:
                details['failure_reason'] = failure_reason

            return self.create_result(
                name='postgres_health',
                value=1,
                status=status,
                details=details
            )
            
        except OperationalError as e:
            return self.create_result(
                name='postgres_health',
                value=0,
                status='fail',
                details={'error': f'PostgreSQL connection failed: {str(e)}'}
            )
        except ImportError:
            return self.create_result(
                name='postgres_health',
                value=0,
                status='error',
                details={'error': 'psycopg2 library not installed'}
            )
