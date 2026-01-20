# Apache Configuration for SSE (Server-Sent Events)

## 🔧 Problem
SSE works on localhost but fails on VPS. This is usually because Apache buffers SSE responses.

## ✅ Solution: Update Apache Configuration

### Option 1: Using mod_proxy (Recommended)

Add this to your Apache virtual host configuration:

```apache
<VirtualHost *:443>
    ServerName stock.junaidworld.com
    
    # ... your SSL and other config ...
    
    # SSE endpoint - disable buffering
    <LocationMatch "^/api/stock-stream/">
        ProxyPass http://127.0.0.1:5000/api/stock-stream/
        ProxyPassReverse http://127.0.0.1:5000/api/stock-stream/
        
        # Disable buffering for SSE
        ProxySet flushpackets=on
        ProxySet disablereuse=off
        
        # Headers
        ProxyPreserveHost On
        RequestHeader set X-Forwarded-Proto "https"
        RequestHeader set X-Forwarded-Port "443"
        
        # Timeouts for long-lived connections
        Timeout 86400
        ProxyTimeout 86400
    </LocationMatch>
    
    # Regular proxy for other endpoints
    ProxyPass / http://127.0.0.1:5000/
    ProxyPassReverse / http://127.0.0.1:5000/
    ProxyPreserveHost On
</VirtualHost>
```

### Option 2: Using mod_headers

If Option 1 doesn't work, add headers:

```apache
<LocationMatch "^/api/stock-stream/">
    Header set Cache-Control "no-cache"
    Header set Connection "keep-alive"
    Header set X-Accel-Buffering "no"
</LocationMatch>
```

## 🔄 After Updating Apache

1. Test configuration:
   ```bash
   sudo apache2ctl configtest
   # OR
   sudo httpd -t
   ```

2. Reload Apache:
   ```bash
   sudo systemctl reload apache2
   # OR
   sudo service apache2 reload
   ```

## 📝 Key Settings Explained

- **`flushpackets=on`**: Sends data immediately without buffering
- **`disablereuse=off`**: Allows connection reuse
- **`Timeout 86400`**: 24-hour timeout for SSE connections
- **`ProxyTimeout 86400`**: Proxy timeout for long connections

## 🧪 Test After Configuration

Open browser console and check:
- Should see: `[SSE] Connected to real-time updates`
- Should see: `[SSE] Connection confirmed for branch: DIP`
- Should NOT see connection errors
