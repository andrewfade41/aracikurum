import os
import sys
import json
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Import curl_cffi for bypassing Cloudflare
try:
    from curl_cffi import requests as curl_requests
except ImportError:
    print("Error: curl_cffi is not installed. Please install it first.")
    sys.exit(1)

# Configuration from environment variables
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
try:
    SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
except ValueError:
    SMTP_PORT = 465
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")
EMAIL_FROM = os.getenv("EMAIL_FROM", "Fintables Analist Takip Botu")

# Configurable lookback period (default 24 hours)
try:
    LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "24"))
except ValueError:
    LOOKBACK_HOURS = 24

# Mapping for rating recommendation types to Turkish descriptions
TYPE_TRANSLATIONS = {
    'al': 'Al',
    'tut': 'Tut',
    'sat': 'Sat',
    'endeks_ustu': 'Endeks Üstü Getiri',
    'endekse_paralel': 'Endekse Paralel Getiri',
    'endeks_alti': 'Endeks Altı Getiri',
    None: 'Belirtilmemiş',
    'None': 'Belirtilmemiş'
}

# Colors for recommendation badges
TYPE_COLORS = {
    'al': '#10b981',        # Green
    'endeks_ustu': '#10b981', # Green
    'tut': '#f59e0b',       # Amber/Orange
    'endekse_paralel': '#f59e0b', # Amber
    'sat': '#ef4444',       # Red
    'endeks_alti': '#ef4444', # Red
    None: '#6b7280',        # Gray
    'None': '#6b7280'
}

def fetch_ratings():
    """Fetches analyst ratings from Fintables API."""
    url = "https://api.fintables.com/analyst-ratings/"
    headers = {
        'accept': 'application/json, text-plain, */*',
        'accept-language': 'en-US,en;q=0.9',
        'origin': 'https://fintables.com',
        'referer': 'https://fintables.com/',
        'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1',
    }
    params = {
        'brokerage_id': '',
        'code': '',
        'in_model_portfolio': '',
    }
    
    print(f"Fetching ratings from {url}...")
    response = curl_requests.get(url, params=params, headers=headers, impersonate='chrome')
    
    if response.status_code != 200:
        raise Exception(f"Failed to fetch data from API. Status: {response.status_code}, Body: {response.text[:500]}")
    
    data = response.json()
    return data.get('results', [])

def process_ratings(results, lookback_hours):
    """Processes ratings, groups them, and filters recent ones."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)
    
    # Group all active ratings by stock code to calculate current consensus
    all_by_stock = {}
    for item in results:
        stock = item.get('code')
        if not stock:
            continue
        if stock not in all_by_stock:
            all_by_stock[stock] = []
        all_by_stock[stock].append(item)
        
    recent_updates = []
    
    # Find updates in the lookback window
    for item in results:
        pub_str = item.get('published_at')
        if not pub_str:
            continue
        pub_dt = datetime.fromisoformat(pub_str.replace('Z', '+00:00'))
        if pub_dt >= cutoff:
            recent_updates.append(item)
            
    # Sort recent updates by publication date descending
    recent_updates.sort(key=lambda x: x.get('published_at', ''), reverse=True)
    
    # Group recent updates by stock
    recent_by_stock = {}
    for item in recent_updates:
        stock = item.get('code')
        if stock not in recent_by_stock:
            recent_by_stock[stock] = []
        recent_by_stock[stock].append(item)
        
    return recent_by_stock, all_by_stock

def generate_html_report(recent_by_stock, all_by_stock, lookback_hours):
    """Generates a styled HTML email body."""
    now_local = datetime.now(timezone(timedelta(hours=3))) # Turkey Time (UTC+3)
    formatted_date = now_local.strftime("%d.%m.%Y %H:%M")
    
    title = f"Aracı Kurum Hedef Fiyat Güncellemeleri - {formatted_date}"
    
    # General CSS styles for responsive premium emails
    css_styles = """
    body {
        font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, Helvetica, Arial, sans-serif;
        background-color: #f1f5f9;
        color: #1e293b;
        margin: 0;
        padding: 0;
        -webkit-font-smoothing: antialiased;
    }
    .wrapper {
        width: 100%;
        background-color: #f1f5f9;
        padding: 40px 20px;
        box-sizing: border-box;
    }
    .container {
        max-width: 680px;
        margin: 0 auto;
        background-color: #ffffff;
        border-radius: 16px;
        overflow: hidden;
        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.05), 0 4px 6px -4px rgba(0, 0, 0, 0.05);
        border: 1px solid #e2e8f0;
    }
    .header {
        background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%);
        color: #ffffff;
        padding: 32px 24px;
        text-align: center;
    }
    .header h1 {
        margin: 0 0 8px 0;
        font-size: 24px;
        font-weight: 700;
        letter-spacing: -0.5px;
    }
    .header p {
        margin: 0;
        font-size: 14px;
        opacity: 0.9;
    }
    .content {
        padding: 32px 24px;
    }
    .info-box {
        background-color: #eff6ff;
        border-left: 4px solid #3b82f6;
        padding: 16px;
        border-radius: 0 8px 8px 0;
        margin-bottom: 24px;
        font-size: 14px;
        color: #1e3a8a;
    }
    .stock-card {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        margin-bottom: 24px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.02);
    }
    .stock-header {
        background-color: #f8fafc;
        padding: 16px 20px;
        border-bottom: 1px solid #e2e8f0;
        border-top-left-radius: 12px;
        border-top-right-radius: 12px;
    }
    .stock-ticker {
        font-size: 20px;
        font-weight: 700;
        color: #1e3a8a;
        display: inline-block;
        vertical-align: middle;
    }
    .consensus-summary {
        background-color: #f8fafc;
        border-top: 1px solid #e2e8f0;
        padding: 16px 20px;
        border-bottom-left-radius: 12px;
        border-bottom-right-radius: 12px;
    }
    .stat-grid {
        width: 100%;
        border-collapse: collapse;
    }
    .stat-cell {
        padding: 4px 8px;
        text-align: center;
        width: 25%;
    }
    .stat-label {
        font-size: 11px;
        text-transform: uppercase;
        color: #64748b;
        font-weight: 600;
        margin-bottom: 4px;
    }
    .stat-value {
        font-size: 16px;
        font-weight: 700;
        color: #0f172a;
    }
    .ratings-table {
        width: 100%;
        border-collapse: collapse;
        margin: 0;
    }
    .ratings-table th {
        background-color: #f8fafc;
        font-size: 11px;
        text-transform: uppercase;
        color: #64748b;
        font-weight: 600;
        padding: 12px 20px;
        text-align: left;
        border-bottom: 1px solid #e2e8f0;
    }
    .ratings-table td {
        padding: 14px 20px;
        font-size: 13.5px;
        border-bottom: 1px solid #f1f5f9;
        vertical-align: middle;
    }
    .ratings-table tr:last-child td {
        border-bottom: none;
    }
    .badge {
        display: inline-block;
        padding: 3px 8px;
        font-size: 11px;
        font-weight: 600;
        border-radius: 6px;
        color: #ffffff;
        text-align: center;
    }
    .footer {
        padding: 24px;
        text-align: center;
        font-size: 12px;
        color: #64748b;
        border-top: 1px solid #f1f5f9;
    }
    .empty-state {
        text-align: center;
        padding: 48px 24px;
        color: #64748b;
    }
    .empty-state h3 {
        margin: 0 0 8px 0;
        color: #475569;
        font-size: 18px;
    }
    .all-ratings-title {
        font-size: 12px;
        font-weight: 600;
        color: #64748b;
        margin: 16px 20px 8px 20px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .broker-logo {
        width: 20px;
        height: 20px;
        border-radius: 4px;
        vertical-align: middle;
        margin-right: 8px;
        display: inline-block;
    }
    """
    
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        <style>{css_styles}</style>
    </head>
    <body>
        <div class="wrapper">
            <div class="container">
                <div class="header">
                    <h1>Fintables Analist Takip Botu</h1>
                    <p>Son {lookback_hours} Saat İçerisinde Eklenen ve Güncellenen Hedef Fiyatlar</p>
                </div>
                <div class="content">
    """
    
    if not recent_by_stock:
        # If no updates, display an empty state report
        html_body += f"""
                    <div class="empty-state">
                        <h3>Son {lookback_hours} Saat İçinde Yeni Hedef Fiyat Bulunamadı</h3>
                        <p>Aracı kurumlar tarafından son 24 saat içerisinde herhangi bir hisse için yeni hedef fiyat veya tavsiye güncellemesi yayınlanmadı.</p>
                    </div>
        """
        
        # As an enhancement, show the last 5 updates overall in the system so the mail isn't empty
        all_updates = []
        for stock, items in all_by_stock.items():
            all_updates.extend(items)
        all_updates.sort(key=lambda x: x.get('published_at', ''), reverse=True)
        
        if all_updates:
            html_body += """
                    <div class="info-box" style="margin-top: 24px; background-color: #f8fafc; border-left-color: #64748b; color: #334155;">
                        <strong>Sistemdeki Son Hedef Fiyat Güncellemeleri:</strong>
                    </div>
                    <div class="stock-card">
                        <table class="ratings-table">
                            <thead>
                                <tr>
                                    <th>Hisse</th>
                                    <th>Aracı Kurum</th>
                                    <th>Hedef Fiyat</th>
                                    <th>Tavsiye</th>
                                    <th>Tarih</th>
                                </tr>
                            </thead>
                            <tbody>
            """
            for item in all_updates[:10]:
                stock = item.get('code', '-')
                broker = item.get('brokerage', {}).get('short_title') or item.get('brokerage', {}).get('title', '-')
                target = item.get('price_target')
                target_str = f"{target:.2f} TL" if target else "Belirtilmemiş"
                rec_type = item.get('type')
                rec_text = TYPE_TRANSLATIONS.get(rec_type, 'Belirtilmemiş')
                badge_color = TYPE_COLORS.get(rec_type, '#6b7280')
                pub_str = item.get('published_at', '')
                pub_date = "-"
                if pub_str:
                    try:
                        pub_dt = datetime.fromisoformat(pub_str.replace('Z', '+00:00'))
                        # convert to Turkey Time
                        pub_dt_tr = pub_dt.astimezone(timezone(timedelta(hours=3)))
                        pub_date = pub_dt_tr.strftime("%d.%m.%Y")
                    except Exception:
                        pub_date = pub_str[:10]
                        
                html_body += f"""
                                <tr>
                                    <td style="font-weight: 700; color: #1e3a8a;">{stock}</td>
                                    <td>{broker}</td>
                                    <td style="font-weight: 600;">{target_str}</td>
                                    <td><span class="badge" style="background-color: {badge_color};">{rec_text}</span></td>
                                    <td style="color: #64748b; font-size: 12px;">{pub_date}</td>
                                </tr>
                """
            html_body += """
                            </tbody>
                        </table>
                    </div>
            """
    else:
        html_body += f"""
                    <div class="info-box">
                        Toplam <strong>{len(recent_by_stock)}</strong> farklı hissede hedef fiyat güncellemesi tespit edildi.
                    </div>
        """
        
        for stock, updates in recent_by_stock.items():
            all_ratings = all_by_stock.get(stock, [])
            
            # Calculate Consensus Stats
            targets = [r.get('price_target') for r in all_ratings if r.get('price_target') is not None]
            avg_target = sum(targets) / len(targets) if targets else 0
            min_target = min(targets) if targets else 0
            max_target = max(targets) if targets else 0
            num_brokers = len(all_ratings)
            
            avg_target_str = f"{avg_target:.2f} TL" if avg_target else "-"
            min_target_str = f"{min_target:.2f} TL" if min_target else "-"
            max_target_str = f"{max_target:.2f} TL" if max_target else "-"
            
            html_body += f"""
                    <div class="stock-card">
                        <div class="stock-header">
                            <span class="stock-ticker">{stock}</span>
                            <span style="float: right; color: #64748b; font-size: 12px; margin-top: 6px;">Güncelleme Sayısı: {len(updates)}</span>
                            <div style="clear: both;"></div>
                        </div>
                        
                        <!-- Today's Updates Table -->
                        <table class="ratings-table">
                            <thead>
                                <tr>
                                    <th>Yeni Güncelleyen Aracı Kurum</th>
                                    <th>Yeni Hedef Fiyat</th>
                                    <th>Tavsiye</th>
                                    <th>Saat</th>
                                </tr>
                            </thead>
                            <tbody>
            """
            
            for item in updates:
                broker = item.get('brokerage', {}).get('title', '-')
                broker_short = item.get('brokerage', {}).get('short_title') or broker
                logo_url = item.get('brokerage', {}).get('logo')
                target = item.get('price_target')
                target_str = f"{target:.2f} TL" if target else "Belirtilmemiş"
                rec_type = item.get('type')
                rec_text = TYPE_TRANSLATIONS.get(rec_type, 'Belirtilmemiş')
                badge_color = TYPE_COLORS.get(rec_type, '#6b7280')
                pub_str = item.get('published_at', '')
                pub_time = "-"
                if pub_str:
                    try:
                        pub_dt = datetime.fromisoformat(pub_str.replace('Z', '+00:00'))
                        pub_dt_tr = pub_dt.astimezone(timezone(timedelta(hours=3)))
                        pub_time = pub_dt_tr.strftime("%H:%M")
                    except Exception:
                        pass
                
                logo_html = f'<img src="{logo_url}" class="broker-logo" alt="">' if logo_url else ''
                html_body += f"""
                                <tr>
                                    <td style="font-weight: 600;">{logo_html}{broker_short}</td>
                                    <td style="font-weight: 700; color: #1e3a8a;">{target_str}</td>
                                    <td><span class="badge" style="background-color: {badge_color};">{rec_text}</span></td>
                                    <td style="color: #64748b; font-size: 12px;">{pub_time}</td>
                                </tr>
                """
                
            html_body += """
                            </tbody>
                        </table>
            """
            
            # Show other brokers tracking this stock if there are any
            other_ratings = [r for r in all_ratings if r not in updates]
            if other_ratings:
                # Sort other ratings by date descending
                other_ratings.sort(key=lambda x: x.get('published_at', ''), reverse=True)
                html_body += f"""
                        <div class="all-ratings-title">Takip Eden Diğer Kurumlar ({len(other_ratings)})</div>
                        <table class="ratings-table" style="background-color: #fafbfc; border-top: 1px solid #f1f5f9;">
                            <tbody>
                """
                for item in other_ratings:
                    broker = item.get('brokerage', {}).get('title', '-')
                    broker_short = item.get('brokerage', {}).get('short_title') or broker
                    logo_url = item.get('brokerage', {}).get('logo')
                    target = item.get('price_target')
                    target_str = f"{target:.2f} TL" if target else "-"
                    rec_type = item.get('type')
                    rec_text = TYPE_TRANSLATIONS.get(rec_type, 'Belirtilmemiş')
                    badge_color = TYPE_COLORS.get(rec_type, '#6b7280')
                    pub_str = item.get('published_at', '')
                    pub_date = "-"
                    if pub_str:
                        try:
                            pub_dt = datetime.fromisoformat(pub_str.replace('Z', '+00:00'))
                            pub_dt_tr = pub_dt.astimezone(timezone(timedelta(hours=3)))
                            pub_date = pub_dt_tr.strftime("%d.%m.%Y")
                        except Exception:
                            pub_date = pub_str[:10]
                            
                    logo_html = f'<img src="{logo_url}" class="broker-logo" style="opacity: 0.8;" alt="">' if logo_url else ''
                    html_body += f"""
                                <tr style="background-color: transparent;">
                                    <td style="padding: 8px 20px; font-size: 12.5px; color: #475569;">{logo_html}{broker_short}</td>
                                    <td style="padding: 8px 20px; font-size: 12.5px; font-weight: 600; color: #475569;">{target_str}</td>
                                    <td style="padding: 8px 20px;"><span class="badge" style="background-color: {badge_color}; opacity: 0.85; padding: 2px 6px; font-size: 10px;">{rec_text}</span></td>
                                    <td style="padding: 8px 20px; color: #94a3b8; font-size: 11px; text-align: right;">{pub_date}</td>
                                </tr>
                    """
                html_body += """
                            </tbody>
                        </table>
                """
            
            # Consensus Stats Grid
            html_body += f"""
                        <div class="consensus-summary">
                            <table class="stat-grid">
                                <tr>
                                    <td class="stat-cell">
                                        <div class="stat-label">Ortalama Hedef</div>
                                        <div class="stat-value" style="color: #3b82f6;">{avg_target_str}</div>
                                    </td>
                                    <td class="stat-cell" style="border-left: 1px solid #e2e8f0;">
                                        <div class="stat-label">En Yüksek Hedef</div>
                                        <div class="stat-value">{max_target_str}</div>
                                    </td>
                                    <td class="stat-cell" style="border-left: 1px solid #e2e8f0;">
                                        <div class="stat-label">En Düşük Hedef</div>
                                        <div class="stat-value">{min_target_str}</div>
                                    </td>
                                    <td class="stat-cell" style="border-left: 1px solid #e2e8f0;">
                                        <div class="stat-label">Kurum Sayısı</div>
                                        <div class="stat-value">{num_brokers}</div>
                                    </td>
                                </tr>
                            </table>
                        </div>
                    </div>
            """
            
    html_body += f"""
                </div>
                <div class="footer">
                    <p>Bu e-posta Fintables verileri taranarak otomatik olarak üretilmiştir.</p>
                    <p>Raporlama Zamanı: {formatted_date} (TR Saati)</p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html_body

def send_email(subject, html_content):
    """Sends the formatted HTML email using configured SMTP settings."""
    if not SMTP_USERNAME or not SMTP_PASSWORD or not EMAIL_TO:
        print("Warning: SMTP credentials or recipient email not configured. E-mail sending skipped.")
        print("Please configure: SMTP_USERNAME, SMTP_PASSWORD, EMAIL_TO")
        # Save HTML to a local file for debug/inspection
        output_file = "last_report.html"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"Report HTML has been saved locally to: {os.path.abspath(output_file)}")
        return False
        
    recipients = [email.strip() for email in EMAIL_TO.split(",")]
    
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"{EMAIL_FROM} <{SMTP_USERNAME}>"
    msg['To'] = ", ".join(recipients)
    
    msg.attach(MIMEText(html_content, 'html'))
    
    try:
        print(f"Connecting to SMTP server {SMTP_SERVER}:{SMTP_PORT}...")
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=15)
        else:
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15)
            server.starttls()
            
        print("Logging in to SMTP server...")
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        
        print(f"Sending email to {len(recipients)} recipient(s)...")
        server.sendmail(SMTP_USERNAME, recipients, msg.as_string())
        server.quit()
        print("Email sent successfully!")
        return True
    except Exception as e:
        print(f"Error occurred while sending email: {e}")
        # Save to local file as fallback so data isn't lost
        with open("error_report_fallback.html", "w", encoding="utf-8") as f:
            f.write(html_content)
        print("Failed report HTML saved to error_report_fallback.html")
        raise e

def main():
    try:
        results = fetch_ratings()
        print(f"Successfully fetched {len(results)} analyst ratings.")
        
        recent_by_stock, all_by_stock = process_ratings(results, LOOKBACK_HOURS)
        print(f"Found {len(recent_by_stock)} stocks with updates in the last {LOOKBACK_HOURS} hours.")
        
        # Build Subject Line
        now_local = datetime.now(timezone(timedelta(hours=3))) # Turkey Time
        date_str = now_local.strftime("%d.%m.%Y")
        
        if recent_by_stock:
            stock_tickers = ", ".join(list(recent_by_stock.keys())[:4])
            if len(recent_by_stock) > 4:
                stock_tickers += "..."
            subject = f"Analist Raporu: {len(recent_by_stock)} Hisse Güncellendi ({stock_tickers}) - {date_str}"
        else:
            subject = f"Analist Raporu: Yeni Güncelleme Yok - {date_str}"
            
        html_content = generate_html_report(recent_by_stock, all_by_stock, LOOKBACK_HOURS)
        
        send_email(subject, html_content)
        
    except Exception as e:
        print(f"Bot failed with error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
