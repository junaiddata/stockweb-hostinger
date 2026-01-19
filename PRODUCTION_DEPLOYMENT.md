# Production Deployment Checklist

## 🚀 Moving to Production: stock.junaidworld.com

This document lists all changes needed when deploying to production on Hostinger VPS.

---

## 📋 Required Changes

### 1. **app.py** - Flask Application

#### Change 1: API Key (Line ~1422)
```python
# BEFORE (Testing):
VPS_API_KEY = "test-key-12345"

# AFTER (Production):
VPS_API_KEY = "your-secure-random-key-here-min-32-chars"
```

**Action:** Generate a secure random key (at least 32 characters). You can use:
```python
import secrets
print(secrets.token_urlsafe(32))
```

#### Change 2: Debug Mode (Line ~2585)
```python
# BEFORE (Testing):
app.run(host='0.0.0.0', port=5000, debug=True)

# AFTER (Production):
app.run(host='0.0.0.0', port=5000, debug=False)
```

**Action:** Change `debug=True` to `debug=False`

---

### 2. **sync_stock_pc.py** - PC Sync Script

#### Change 1: VPS URL (Line ~41)
```python
# BEFORE (Testing):
VPS_BASE_URL = "http://localhost:5000"

# AFTER (Production):
VPS_BASE_URL = "https://stock.junaidworld.com"
```

**Action:** Change to your production domain

#### Change 2: API Key (Line ~42)
```python
# BEFORE (Testing):
VPS_API_KEY = "test-key-12345"

# AFTER (Production):
VPS_API_KEY = "your-secure-random-key-here-min-32-chars"
```

**Action:** Must match the key in `app.py` exactly!

---

## ✅ Quick Checklist

### On VPS (Hostinger):

- [ ] **Change `app.py` line 1422**: Update `VPS_API_KEY` to secure random key
- [ ] **Change `app.py` line 2585**: Set `debug=False`
- [ ] **Verify Flask is running**: Check if app is accessible at `https://stock.junaidworld.com`
- [ ] **Check SSL/HTTPS**: Ensure HTTPS is working (required for production)
- [ ] **Database permissions**: Ensure SQLite databases are writable
- [ ] **Log directory**: Ensure log files can be written

### On PC (Local Machine):

- [ ] **Change `sync_stock_pc.py` line 41**: Update `VPS_BASE_URL` to `https://stock.junaidworld.com`
- [ ] **Change `sync_stock_pc.py` line 42**: Update `VPS_API_KEY` to match `app.py`
- [ ] **Test sync**: Run sync script manually to verify connection
- [ ] **Task Scheduler**: Verify Task Scheduler is configured correctly

---

## 🔐 Security Checklist

- [ ] **API Key**: Use strong random key (32+ characters)
- [ ] **HTTPS**: Ensure SSL certificate is valid
- [ ] **Debug Mode**: Disabled in production
- [ ] **Database**: SQLite files have correct permissions
- [ ] **Logs**: Check log files don't contain sensitive data
- [ ] **Firewall**: Only necessary ports open (80, 443, 5000 if needed)

---

## 🌐 Domain & SSL Configuration

### If using reverse proxy (Nginx/Apache):

**Nginx Example:**
```nginx
server {
    listen 80;
    server_name stock.junaidworld.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name stock.junaidworld.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Important:** If using reverse proxy, Flask app should still run on `0.0.0.0:5000`

---

## 🧪 Testing After Deployment

### 1. Test Web Access
- [ ] Open `https://stock.junaidworld.com` in browser
- [ ] Verify home page loads
- [ ] Test login functionality
- [ ] Test stock search

### 2. Test API Sync
- [ ] Run `sync_stock_pc.py` manually from PC
- [ ] Check sync log: `sync_stock.log`
- [ ] Verify data appears on website
- [ ] Check for errors in Flask logs

### 3. Test Real-Time Updates
- [ ] Open stock page in browser
- [ ] Run sync script
- [ ] Verify update banner appears
- [ ] Verify page updates correctly

---

## 📝 Configuration Summary

### Production Values:

| Setting | Value |
|---------|-------|
| **Domain** | `stock.junaidworld.com` |
| **Protocol** | `https://` |
| **VPS URL** | `https://stock.junaidworld.com` |
| **API Key** | `[Generate secure random key]` |
| **Debug Mode** | `False` |
| **Port** | `5000` (or configured port) |

---

## 🔄 Deployment Steps

1. **Backup current code** (if updating existing deployment)
2. **Update `app.py`**:
   - Change API key
   - Disable debug mode
3. **Update `sync_stock_pc.py`**:
   - Change VPS URL
   - Change API key (match app.py)
4. **Restart Flask app** on VPS
5. **Test sync** from PC
6. **Verify** website functionality

---

## ⚠️ Important Notes

1. **API Key Security**: 
   - Never commit API keys to Git
   - Use environment variables if possible
   - Keep keys secret and rotate periodically

2. **HTTPS Required**: 
   - Production must use HTTPS
   - Update `VPS_BASE_URL` to use `https://`

3. **Port Configuration**:
   - Flask runs on port 5000 internally
   - External access via port 443 (HTTPS) through reverse proxy
   - Ensure firewall allows necessary ports

4. **Database Backups**:
   - SQLite databases should be backed up regularly
   - Consider automated backup script

5. **Log Monitoring**:
   - Monitor `sync_stock.log` on PC
   - Monitor Flask logs on VPS
   - Set up log rotation if needed

---

## 🆘 Troubleshooting

### Sync Script Can't Connect:
- Check `VPS_BASE_URL` is correct
- Verify HTTPS is working
- Check firewall rules
- Verify API key matches

### Website Not Loading:
- Check Flask is running: `ps aux | grep python`
- Check port 5000 is accessible
- Verify reverse proxy configuration
- Check SSL certificate validity

### API Sync Fails:
- Check API key matches in both files
- Verify network connectivity from PC to VPS
- Check Flask logs for errors
- Verify database permissions

---

## 📞 Post-Deployment

After deployment, monitor:
- [ ] Sync script runs successfully every 2 minutes
- [ ] Website loads correctly
- [ ] Real-time updates work
- [ ] No errors in logs
- [ ] Database updates correctly

---

**Last Updated:** 2026-01-19
**Production Domain:** stock.junaidworld.com
