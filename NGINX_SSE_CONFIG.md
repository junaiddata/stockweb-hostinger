# Nginx Configuration for SSE (Server-Sent Events)

## 🔧 Problem
SSE works on localhost but fails on VPS. This is usually because Nginx (reverse proxy) buffers SSE responses.

## ✅ Solution: Update Nginx Configuration

Add these settings to your Nginx config for the SSE endpoint:

### Location Block for SSE Endpoint

Add this to your Nginx server block:

```nginx
server {
    listen 443 ssl http2;
    server_name stock.junaidworld.com;
    
    # ... your SSL and other config ...
    
    # SSE endpoint - disable buffering
    location /api/stock-stream/ {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        
        # Disable buffering for SSE
        proxy_buffering off;
        proxy_cache off;
        
        # Headers for SSE
        proxy_set_header Connection '';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeouts for long-lived connections
        proxy_read_timeout 24h;
        proxy_send_timeout 24h;
        
        # Disable Nginx buffering
        proxy_request_buffering off;
        chunked_transfer_encoding on;
    }
    
    # Regular location for other endpoints
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 🔄 After Updating Nginx

1. Test configuration:
   ```bash
   sudo nginx -t
   ```

2. Reload Nginx:
   ```bash
   sudo systemctl reload nginx
   # OR
   sudo service nginx reload
   ```

3. Test SSE connection in browser console

## 📝 Key Settings Explained

- **`proxy_buffering off`**: Prevents Nginx from buffering SSE responses
- **`proxy_cache off`**: Disables caching for SSE
- **`proxy_read_timeout 24h`**: Keeps connection alive for 24 hours
- **`chunked_transfer_encoding on`**: Enables chunked encoding for SSE
- **`X-Accel-Buffering: no`**: Header sent by Flask to disable buffering

## 🧪 Test After Configuration

Open browser console and check:
- Should see: `[SSE] Connected to real-time updates`
- Should see: `[SSE] Connection confirmed for branch: DIP`
- Should NOT see connection errors

## ⚠️ If Still Not Working

1. Check Nginx error logs:
   ```bash
   sudo tail -f /var/log/nginx/error.log
   ```

2. Check Flask logs for errors

3. Verify Flask is running:
   ```bash
   ps aux | grep python
   ```

4. Test direct connection (bypass Nginx):
   ```bash
   curl -N http://127.0.0.1:5000/api/stock-stream/DIP
   ```
   Should see SSE data streaming
