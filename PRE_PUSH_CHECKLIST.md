# ✅ Pre-Push Checklist - Final Check

## 🔴 CRITICAL: Must Fix Before Push

### 1. **app.py** - Line 1422
**Current:**
```python
VPS_API_KEY = "YOUR_Junaid6231#_RANDOM_KEY_HERE_32_CHARS_MIN"
```

**Action:** Replace with REAL secure key (generate below)

---

### 2. **app.py** - Line 2585
**Current:**
```python
app.run(host='0.0.0.0', port=5000 , debug=True)
```

**Action:** Change to:
```python
app.run(host='0.0.0.0', port=5000, debug=False)
```

---

### 3. **sync_stock_pc.py** - Line 42
**Current:**
```python
VPS_API_KEY = "YOUR_Junaid6231#_RANDOM_KEY_HERE_32_CHARS_MIN"
```

**Action:** Replace with SAME key as app.py (must match exactly!)

---

## ✅ Already Correct

- ✅ **sync_stock_pc.py** Line 41: `VPS_BASE_URL = "https://stock.junaidworld.com"` ✓
- ✅ Domain and SSL already configured ✓

---

## 🔑 Generate Secure API Key

Run this in Python to generate a secure key:

```python
import secrets
api_key = secrets.token_urlsafe(32)
print(api_key)
```

**Copy the output** and use it in BOTH files.

---

## 📝 Quick Fix Steps

1. **Generate API key** (use command above)
2. **Update app.py line 1422**: Replace placeholder with generated key
3. **Update app.py line 2585**: Change `debug=True` to `debug=False`
4. **Update sync_stock_pc.py line 42**: Use SAME key as app.py
5. **Verify**: Both files have identical API key
6. **Push to production**

---

## ⚠️ Important

- API key MUST be the same in both files
- Debug mode MUST be False in production
- VPS URL is already correct ✓

---

**Status:** 2 files need changes, 3 lines total
