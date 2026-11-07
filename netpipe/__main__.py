import sys

# Windows consoles on the legacy cp1252 code page can't print ✓ and ✗.
# See "Windows: UnicodeEncodeError on the Tick Characters" in chapter 1.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from netpipe.cli import main

sys.exit(main())
