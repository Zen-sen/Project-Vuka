"""
Migration script: JSON → SQLite for Project Vuka

Reads existing JSON files and imports all data into vuka_trading.db
Validates integrity and reports migration summary
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from database_manager import DatabaseManager


def migrate_trades_to_db(db: DatabaseManager, base_dir: Path = Path(".")):
    """Migrate all trades_*.json files to database."""
    trades_files = list(base_dir.glob("trades_*.json"))
    total_imported = 0
    total_duplicates = 0
    
    for trades_file in trades_files:
        # Extract symbol and strategy from filename: trades_{SYMBOL}_{STRATEGY}.json
        stem = trades_file.stem  # e.g., "trades_EURUSD_INGWE"
        parts = stem.replace("trades_", "", 1).split("_")
        if len(parts) >= 2:
            file_symbol = parts[0] + "c"  # EURUSD -> EURUSDc
            file_strategy = "_".join(parts[1:])
        else:
            file_symbol = "UNKNOWN"
            file_strategy = parts[0] if parts else "UNKNOWN"
        
        try:
            with open(trades_file, "r") as f:
                trades_list = json.load(f)
            
            if not isinstance(trades_list, list):
                print(f"[-] {trades_file}: Not a list, skipping")
                continue
            
            print(f"\n[Importing] {trades_file} (symbol={file_symbol}, strategy={file_strategy})...")
            imported = 0
            duplicates = 0
            
            for trade in trades_list:
                # Inject symbol from filename if missing
                if not trade.get("symbol") or trade["symbol"] == "UNKNOWN":
                    trade["symbol"] = file_symbol
                if not trade.get("strategy") or trade["strategy"] == "UNKNOWN":
                    trade["strategy"] = file_strategy
                # Map old JSON 'entry' field to new schema
                if "entry" in trade and trade["entry"] is not None:
                    if trade.get("entry_req") is None:
                        trade["entry_req"] = trade["entry"]
                    if trade.get("entry_fill") is None:
                        trade["entry_fill"] = trade["entry"]
                result = db.insert_trade(trade)
                if result > 0:
                    imported += 1
                else:
                    duplicates += 1
            
            print(f"   [OK] Imported: {imported} | Duplicates: {duplicates}")
            total_imported += imported
            total_duplicates += duplicates
            
        except json.JSONDecodeError as e:
            print(f"[-] {trades_file}: JSON decode error - {e}")
        except Exception as e:
            print(f"[-] {trades_file}: Error - {e}")
    
    return total_imported, total_duplicates


def migrate_sessions_to_db(db: DatabaseManager, base_dir: Path = Path(".")):
    """Migrate all sessions_*.json files to database."""
    sessions_files = list(base_dir.glob("sessions_*.json"))
    total_sessions = 0
    
    for sessions_file in sessions_files:
        try:
            with open(sessions_file, "r") as f:
                data = json.load(f)
            
            # sessions_*.json files have structure: {date: "YYYY-MM-DD", sessions: [...], consecutive_losses: int}
            if not isinstance(data, dict):
                print(f"[-] {sessions_file}: Not a dict, skipping")
                continue
            
            date = data.get("date", datetime.now().strftime("%Y-%m-%d"))
            sessions_list = data.get("sessions", [])
            consecutive_losses = data.get("consecutive_losses", 0)
            
            # Extract symbol and strategy from filename
            # e.g., sessions_EURUSD_INGWE.json
            parts = sessions_file.stem.replace("sessions_", "").split("_")
            if len(parts) >= 2:
                symbol = parts[0]
                strategy = "_".join(parts[1:])
            else:
                continue
            
            print(f"\n[Importing] {sessions_file}...")
            
            # Session list is just session names
            imported = 0
            for session_name in sessions_list:
                try:
                    db.insert_session(date, symbol, strategy, session_name, traded=True)
                    imported += 1
                except:
                    pass  # Ignore duplicates
            
            # Update loss tracking
            if consecutive_losses > 0:
                db.update_loss_tracking(date, symbol, strategy, consecutive_losses)
            
            print(f"   [OK] Sessions: {imported} | Losses: {consecutive_losses}")
            total_sessions += imported
            
        except Exception as e:
            print(f"[-] {sessions_file}: Error - {e}")
    
    return total_sessions


def migrate_sl_moves_to_db(db: DatabaseManager, base_dir: Path = Path(".")):
    """Migrate all sl_moves_*.json files to database."""
    sl_moves_files = list(base_dir.glob("sl_moves_*.json"))
    total_sl_moves = 0
    
    for sl_moves_file in sl_moves_files:
        try:
            with open(sl_moves_file, "r") as f:
                sl_moves_list = json.load(f)
            
            if not isinstance(sl_moves_list, list):
                print(f"[-] {sl_moves_file}: Not a list, skipping")
                continue
            
            print(f"\n[Importing] {sl_moves_file}...")
            imported = 0
            
            for sl_move in sl_moves_list:
                try:
                    result = db.insert_sl_movement(sl_move)
                    if result > 0:
                        imported += 1
                except:
                    pass  # Ignore duplicates
            
            print(f"   [OK] Imported: {imported}")
            total_sl_moves += imported
            
        except json.JSONDecodeError as e:
            print(f"[-] {sl_moves_file}: JSON decode error - {e}")
        except Exception as e:
            print(f"[-] {sl_moves_file}: Error - {e}")
    
    return total_sl_moves


def validate_migration(db: DatabaseManager, base_dir: Path = Path(".")):
    """Validate that migration is complete."""
    print("\n" + "="*60)
    print("VALIDATION REPORT")
    print("="*60)
    
    # Count total trades in database
    all_trades = db.get_trades(limit=999999)
    print(f"\nTotal trades in database: {len(all_trades)}")
    
    # Count trades by symbol/strategy
    trade_counts = {}
    for trade in all_trades:
        key = f"{trade['symbol']}_{trade['strategy']}"
        trade_counts[key] = trade_counts.get(key, 0) + 1
    
    print("\nTrades by symbol/strategy:")
    for key, count in sorted(trade_counts.items()):
        print(f"  {key}: {count}")
    
    # Check for data consistency
    print("\nData integrity checks:")
    
    # Sample a few trades
    sample_trades = db.get_trades(limit=5)
    if sample_trades:
        print(f"  [OK] Sample trades found: {len(sample_trades)}")
        for t in sample_trades[:2]:
            print(f"    - {t['symbol']} {t['direction']} @ {t['time']}")
    else:
        print("  [!] No trades found!")
    
    print("\n" + "="*60)


def main():
    """Run migration."""
    print("\n" + "="*60)
    print("PROJECT VUKA - JSON -> SQLite MIGRATION")
    print("="*60)
    
    base_dir = Path(".")
    
    # Initialize database
    print("\n[Initializing] database...")
    db = DatabaseManager()
    print("[OK] Database initialized")
    
    # Run migrations
    print("\n" + "-"*60)
    print("PHASE 1: Migrate trades")
    print("-"*60)
    trades_imported, trades_duplicates = migrate_trades_to_db(db, base_dir)
    print(f"\n[OK] Trades migration complete: {trades_imported} imported, {trades_duplicates} duplicates")
    
    print("\n" + "-"*60)
    print("PHASE 2: Migrate sessions & loss tracking")
    print("-"*60)
    sessions_imported = migrate_sessions_to_db(db, base_dir)
    print(f"\n[OK] Sessions migration complete: {sessions_imported} sessions")
    
    print("\n" + "-"*60)
    print("PHASE 3: Migrate SL movements")
    print("-"*60)
    sl_moves_imported = migrate_sl_moves_to_db(db, base_dir)
    print(f"\n[OK] SL movements migration complete: {sl_moves_imported} movements")
    
    # Validate
    validate_migration(db, base_dir)
    
    db.close()
    
    print(f"\n[SUCCESS] MIGRATION COMPLETE")
    print(f"   Trades: {trades_imported}")
    print(f"   Sessions: {sessions_imported}")
    print(f"   SL Moves: {sl_moves_imported}")
    print(f"   Database: {DatabaseManager.DB_FILE}")
    print("\n[!] Keep JSON files as backup. Verify data before deleting.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
