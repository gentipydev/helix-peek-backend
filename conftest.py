"""What every test in this checkout shares, `tests/` and `pipeline/` alike.

The service counts requests a minute and bytes a day per process
(`app/guard.py`), and a test session is one process: left on, a count from one
test would refuse a request in another. So the suite runs with those limits
off, set here before anything imports `app.config`; the tests of the limits
themselves (`tests/test_guard.py`) set their own and start from nothing.
The caps that live in the database (asks, installs, proteins held, the budget)
stay as they are: those are tested where they are counted.
"""

import os

for _name in ("REQUESTS_PER_MINUTE", "POSTS_PER_MINUTE", "SERVICE_REQUESTS_PER_MINUTE",
              "RESPONSE_BYTES_PER_DAY"):
    os.environ.setdefault(_name, "0")
