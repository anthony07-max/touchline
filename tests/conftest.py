import os, tempfile
os.environ.setdefault("TOUCHLINE_DB", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("TOUCHLINE_PBKDF2_ITER", "2000")
