#!/bin/bash
# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
# Backup and Restore Scripts for Monitoring System
# Backs up PostgreSQL, ChromaDB, and configuration files

set -e

# Configuration
BACKUP_DIR="${BACKUP_DIR:-/backup/monitoring}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-monitoring-postgres}"
POSTGRES_DB="${POSTGRES_DB:-monitoring}"
POSTGRES_USER="${POSTGRES_USER:-monitoring}"
CHROMA_DATA_DIR="${CHROMA_DATA_DIR:-/data/monitoring/chromadb}"
CONFIG_DIR="${CONFIG_DIR:-/app/config}"
S3_BUCKET="${S3_BUCKET:-}"  # Optional: S3 bucket for offsite backup

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Print an informational message in green.
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

# Print a warning in yellow.
log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# Print an error in red.
log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Backup PostgreSQL Database
backup_postgres() {
    local backup_file="$1"
    
    log_info "Backing up PostgreSQL database..."
    
    docker exec "$POSTGRES_CONTAINER" pg_dump \
        -U "$POSTGRES_USER" \
        -d "$POSTGRES_DB" \
        --format=custom \
        --compress=9 \
        > "$backup_file"
    
    if [ $? -eq 0 ]; then
        log_info "PostgreSQL backup completed: $backup_file"
        return 0
    else
        log_error "PostgreSQL backup failed"
        return 1
    fi
}

# Backup ChromaDB Vector Database
backup_chromadb() {
    local backup_file="$1"
    
    log_info "Backing up ChromaDB..."
    
    if [ ! -d "$CHROMA_DATA_DIR" ]; then
        log_warn "ChromaDB data directory not found: $CHROMA_DATA_DIR"
        return 1
    fi
    
    tar -czf "$backup_file" -C "$(dirname "$CHROMA_DATA_DIR")" "$(basename "$CHROMA_DATA_DIR")"
    
    if [ $? -eq 0 ]; then
        log_info "ChromaDB backup completed: $backup_file"
        return 0
    else
        log_error "ChromaDB backup failed"
        return 1
    fi
}

# Backup Configuration Files
backup_config() {
    local backup_file="$1"
    
    log_info "Backing up configuration files..."
    
    if [ ! -d "$CONFIG_DIR" ]; then
        log_warn "Config directory not found: $CONFIG_DIR"
        return 1
    fi
    
    tar -czf "$backup_file" -C "$(dirname "$CONFIG_DIR")" "$(basename "$CONFIG_DIR")"
    
    if [ $? -eq 0 ]; then
        log_info "Configuration backup completed: $backup_file"
        return 0
    else
        log_error "Configuration backup failed"
        return 1
    fi
}

# Full System Backup
full_backup() {
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local backup_subdir="$BACKUP_DIR/$timestamp"
    
    log_info "Starting full system backup..."
    
    # Create backup directory
    mkdir -p "$backup_subdir"
    
    # Backup PostgreSQL
    backup_postgres "$backup_subdir/postgres.dump"
    
    # Backup ChromaDB
    backup_chromadb "$backup_subdir/chromadb.tar.gz"
    
    # Backup Configuration
    backup_config "$backup_subdir/config.tar.gz"
    
    # Create backup manifest
    cat > "$backup_subdir/manifest.txt" <<EOF
Monitoring System Backup
Timestamp: $timestamp
Date: $(date)
Hostname: $(hostname)

Components:
- PostgreSQL Database: postgres.dump
- ChromaDB Vector Store: chromadb.tar.gz
- Configuration Files: config.tar.gz

PostgreSQL Info:
- Database: $POSTGRES_DB
- User: $POSTGRES_USER

Retention: $RETENTION_DAYS days
EOF
    
    # Calculate checksums
    (cd "$backup_subdir" && sha256sum * > checksums.txt)
    
    # Create tarball of entire backup
    local backup_tarball="$BACKUP_DIR/monitoring-backup-$timestamp.tar.gz"
    tar -czf "$backup_tarball" -C "$BACKUP_DIR" "$timestamp"
    
    # Upload to S3 if configured
    if [ -n "$S3_BUCKET" ]; then
        log_info "Uploading backup to S3..."
        aws s3 cp "$backup_tarball" "s3://$S3_BUCKET/backups/$(basename "$backup_tarball")"
    fi
    
    log_info "Full backup completed: $backup_tarball"
    
    # Cleanup old backups
    cleanup_old_backups
}

# Restore PostgreSQL
restore_postgres() {
    local backup_file="$1"
    
    log_info "Restoring PostgreSQL database..."
    log_warn "This will overwrite the current database!"
    
    read -p "Are you sure you want to continue? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        log_info "Restore cancelled"
        return 1
    fi
    
    # Drop existing connections
    docker exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d postgres -c \
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$POSTGRES_DB' AND pid <> pg_backend_pid();"
    
    # Restore database
    docker exec -i "$POSTGRES_CONTAINER" pg_restore \
        -U "$POSTGRES_USER" \
        -d "$POSTGRES_DB" \
        --clean \
        --if-exists \
        < "$backup_file"
    
    if [ $? -eq 0 ]; then
        log_info "PostgreSQL restore completed"
        return 0
    else
        log_error "PostgreSQL restore failed"
        return 1
    fi
}

# Restore ChromaDB
restore_chromadb() {
    local backup_file="$1"
    
    log_info "Restoring ChromaDB..."
    log_warn "This will overwrite the current ChromaDB data!"
    
    read -p "Are you sure you want to continue? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        log_info "Restore cancelled"
        return 1
    fi
    
    # Stop ChromaDB container
    docker stop monitoring-chromadb || true
    
    # Remove existing data
    rm -rf "$CHROMA_DATA_DIR"
    
    # Extract backup
    tar -xzf "$backup_file" -C "$(dirname "$CHROMA_DATA_DIR")"
    
    # Start ChromaDB container
    docker start monitoring-chromadb
    
    log_info "ChromaDB restore completed"
}

# Restore Configuration
restore_config() {
    local backup_file="$1"
    
    log_info "Restoring configuration files..."
    
    # Backup current config
    if [ -d "$CONFIG_DIR" ]; then
        mv "$CONFIG_DIR" "${CONFIG_DIR}.bak.$(date +%Y%m%d_%H%M%S)"
    fi
    
    # Extract backup
    tar -xzf "$backup_file" -C "$(dirname "$CONFIG_DIR")"
    
    log_info "Configuration restore completed"
}

# Full System Restore
full_restore() {
    local backup_path="$1"
    
    if [ ! -f "$backup_path" ]; then
        log_error "Backup file not found: $backup_path"
        return 1
    fi
    
    log_info "Starting full system restore from: $backup_path"
    
    # Extract backup tarball
    local restore_dir="/tmp/monitoring-restore-$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$restore_dir"
    
    tar -xzf "$backup_path" -C "$restore_dir"
    
    # Find the backup directory (should be only one)
    local backup_subdir=$(find "$restore_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)
    
    if [ -z "$backup_subdir" ]; then
        log_error "Invalid backup structure"
        return 1
    fi
    
    # Verify checksums
    log_info "Verifying backup integrity..."
    (cd "$backup_subdir" && sha256sum -c checksums.txt)
    
    if [ $? -ne 0 ]; then
        log_error "Checksum verification failed!"
        return 1
    fi
    
    # Restore components
    restore_postgres "$backup_subdir/postgres.dump"
    restore_chromadb "$backup_subdir/chromadb.tar.gz"
    restore_config "$backup_subdir/config.tar.gz"
    
    # Cleanup
    rm -rf "$restore_dir"
    
    log_info "Full restore completed"
    log_warn "Please restart all services to apply changes"
}

# Cleanup old backups
cleanup_old_backups() {
    log_info "Cleaning up backups older than $RETENTION_DAYS days..."
    
    find "$BACKUP_DIR" -name "monitoring-backup-*.tar.gz" -mtime +$RETENTION_DAYS -delete
    find "$BACKUP_DIR" -maxdepth 1 -type d -mtime +$RETENTION_DAYS -exec rm -rf {} \;
    
    log_info "Cleanup completed"
}

# Verify backup
verify_backup() {
    local backup_path="$1"
    
    log_info "Verifying backup: $backup_path"
    
    if [ ! -f "$backup_path" ]; then
        log_error "Backup file not found"
        return 1
    fi
    
    # Extract to temp directory
    local temp_dir="/tmp/backup-verify-$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$temp_dir"
    
    tar -xzf "$backup_path" -C "$temp_dir"
    
    # Find backup directory
    local backup_dir=$(find "$temp_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)
    
    # Verify checksums
    (cd "$backup_dir" && sha256sum -c checksums.txt)
    
    local result=$?
    
    # Cleanup
    rm -rf "$temp_dir"
    
    if [ $result -eq 0 ]; then
        log_info "Backup verification successful"
        return 0
    else
        log_error "Backup verification failed"
        return 1
    fi
}

# List available backups
list_backups() {
    log_info "Available backups in $BACKUP_DIR:"
    echo ""
    
    find "$BACKUP_DIR" -name "monitoring-backup-*.tar.gz" -type f -printf "%T@ %p\n" | \
        sort -rn | \
        while read timestamp file; do
            size=$(du -h "$file" | cut -f1)
            date=$(date -d @"${timestamp%.*}" "+%Y-%m-%d %H:%M:%S")
            echo "  $date  $size  $(basename "$file")"
        done
}

# Main script
case "${1:-}" in
    backup)
        full_backup
        ;;
    restore)
        if [ -z "${2:-}" ]; then
            log_error "Usage: $0 restore <backup-file>"
            exit 1
        fi
        full_restore "$2"
        ;;
    verify)
        if [ -z "${2:-}" ]; then
            log_error "Usage: $0 verify <backup-file>"
            exit 1
        fi
        verify_backup "$2"
        ;;
    list)
        list_backups
        ;;
    cleanup)
        cleanup_old_backups
        ;;
    *)
        echo "Usage: $0 {backup|restore|verify|list|cleanup}"
        echo ""
        echo "Commands:"
        echo "  backup          - Create full system backup"
        echo "  restore <file>  - Restore from backup file"
        echo "  verify <file>   - Verify backup integrity"
        echo "  list            - List available backups"
        echo "  cleanup         - Remove old backups"
        echo ""
        echo "Environment Variables:"
        echo "  BACKUP_DIR      - Backup directory (default: /backup/monitoring)"
        echo "  RETENTION_DAYS  - Backup retention in days (default: 30)"
        echo "  S3_BUCKET       - S3 bucket for offsite backup (optional)"
        exit 1
        ;;
esac
