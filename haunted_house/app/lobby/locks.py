"""One process-wide lock serialising lobby mutations (team joins, leader changes, kicks...).

Single-worker design: this makes "check capacity, then insert" atomic. On PostgreSQL with several
workers the same sections would use ``SELECT ... FOR UPDATE`` row locks instead.
"""
import threading

game_lock = threading.RLock()
