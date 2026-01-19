# Production Changes - Quick Reference

## 🎯 Exact Code Changes Needed

### File 1: `app.py`

#### Change 1: Line ~1422 - API Key
```python
# REPLACE THIS:
VPS_API_KEY = "test-key-12345"  # For testing. Change to secure random key for production

# WITH THIS:
VPS_API_KEY = "YOUR_SECURE_RANDOM_KEY_HERE_32_CHARS_MIN"
```

#### Change 2: Line ~2585 - Debug Mode
```python
# REPLACE THIS:
app.run(host='0.0.0.0', port=5000 , debug=True)

# WITH THIS:
app.run(host='0.0.0.0', port=5000, debug=False)
```

---

### File 2: `sync_stock_pc.py`

#### Change 1: Line ~41 - VPS URL
```python
# REPLACE THIS:
VPS_BASE_URL = "http://localhost:5000"  # For testing: localhost. For production: https://your-vps-domain.com

# WITH THIS:
VPS_BASE_URL = "https://stock.junaidworld.com"
```

#### Change 2: Line ~42 - API Key
```python
# REPLACE THIS:
VPS_API_KEY = "test-key-12345"  # For testing. Must match app.py. Change to secure random key for production

# WITH THIS:
VPS_API_KEY = "YOUR_SECURE_RANDOM_KEY_HERE_32_CHARS_MIN"  # MUST MATCH app.py exactly!
```

---

## 🔑 Generate Secure API Key

Run this Python command to generate a secure key:

```python
import secrets
print(secrets.token_urlsafe(32))
```

Copy the output and use it in **BOTH** files (`app.py` and `sync_stock_pc.py`).

---

## ✅ Summary

**Total Changes:** 4 lines across 2 files

1. ✅ `app.py` line ~1422: Change API key
2. ✅ `app.py` line ~2585: `debug=False`
3. ✅ `sync_stock_pc.py` line ~41: `https://stock.junaidworld.com`
4. ✅ `sync_stock_pc.py` line ~42: Change API key (match app.py)

**That's it!** 🎉
