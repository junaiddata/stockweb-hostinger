# Fix Nginx Proxy Headers Hash Warning

## Warning Message
```
could not build optimal proxy_headers_hash, you should increase either 
proxy_headers_hash_max_size: 512 or proxy_headers_hash_bucket_size: 64
```

## What This Means
Nginx is warning that the proxy headers hash might not be optimal. This happens when you have many proxy headers or long header names.

## Solution

Edit your main Nginx configuration file:
```bash
sudo nano /etc/nginx/nginx.conf
```

Find the `http {` block and add these lines inside it (usually near the top, after other directives):

```nginx
http {
    # ... existing directives ...
    
    # Increase proxy headers hash sizes to eliminate warning
    proxy_headers_hash_max_size 1024;
    proxy_headers_hash_bucket_size 128;
    
    # ... rest of configuration ...
}
```

## Complete Example

Your `/etc/nginx/nginx.conf` should look something like this:

```nginx
user www-data;
worker_processes auto;
pid /run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    # Basic settings
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    
    # Fix proxy headers hash warning
    proxy_headers_hash_max_size 1024;
    proxy_headers_hash_bucket_size 128;
    
    # Include other configs
    include /etc/nginx/mime.types;
    include /etc/nginx/conf.d/*.conf;
    include /etc/nginx/sites-enabled/*;
    
    # ... rest of configuration ...
}
```

## Steps to Apply

1. **Edit main config:**
   ```bash
   sudo nano /etc/nginx/nginx.conf
   ```

2. **Add the two lines** inside the `http {` block:
   ```nginx
   proxy_headers_hash_max_size 1024;
   proxy_headers_hash_bucket_size 128;
   ```

3. **Test configuration:**
   ```bash
   sudo nginx -t
   ```
   Should show: `syntax is ok` and `test is successful` **without warnings**

4. **Reload Nginx:**
   ```bash
   sudo systemctl reload nginx
   ```

## Values Explained

- **`proxy_headers_hash_max_size`**: Maximum size of the hash table for proxy headers (default: 512)
  - Increased to 1024 for better performance
  
- **`proxy_headers_hash_bucket_size`**: Size of buckets in the hash table (default: 64)
  - Increased to 128 to match the larger max_size

## Note

This is **optional** - your configuration works fine with the warning. However, fixing it:
- ✅ Eliminates the warning message
- ✅ Optimizes proxy header handling
- ✅ Prevents potential issues with many/long headers
- ✅ Takes only 2 minutes to fix
