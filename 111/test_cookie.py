# -*- coding: utf-8 -*-
"""Test Chrome cookie extraction with Windows DPAPI decryption"""
import os, sqlite3, shutil, tempfile
import win32crypt

def get_chrome_cookies(domain="agent.tanyuai.com"):
    """Extract cookies from Chrome using Windows DPAPI decryption"""
    # Chrome cookie database paths
    possible_paths = [
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Network\Cookies"),
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cookies"),
    ]
    
    cookies = []
    for db_path in possible_paths:
        if not os.path.exists(db_path):
            continue
        print(f"Found DB: {db_path}")
        
        # Copy to temp to avoid lock
        tmp = os.path.join(tempfile.gettempdir(), "chrome_cookies_temp.db")
        try:
            shutil.copy2(db_path, tmp)
        except Exception as e:
            print(f"Copy failed: {e}")
            continue
        
        try:
            conn = sqlite3.connect(tmp)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT host_key, name, encrypted_value FROM cookies WHERE host_key LIKE ?",
                (f"%{domain}%",)
            )
            rows = cursor.fetchall()
            print(f"Found {len(rows)} raw cookie rows")
            
            for host, name, enc_val in rows:
                try:
                    # Decrypt using Windows DPAPI
                    decrypted = win32crypt.CryptUnprotectData(enc_val, None, None, None, 0)
                    value = decrypted[1].decode("utf-8", errors="replace")
                    cookies.append(f"{name}={value}")
                    print(f"  {name}={value[:30]}...")
                except Exception as e:
                    print(f"  Decrypt failed for {name}: {e}")
            
            conn.close()
        except Exception as e:
            print(f"DB read failed: {e}")
        finally:
            try: os.unlink(tmp)
            except: pass
    
    result = "; ".join(cookies) if cookies else None
    print(f"\nTotal: {len(cookies)} cookies")
    return result

if __name__ == "__main__":
    result = get_chrome_cookies()
    if result:
        print(f"\nCookie string:\n{result}")
    else:
        print("No cookies found")
