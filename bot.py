import os
import sys
import json
import time
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid, formatdate
from email.header import Header

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

def fetch_current_prices(tickers):
    """Fetches current stock prices sequentially from Yahoo Finance (BIST ending with .IS)."""
    prices = {}
    if not tickers:
        return prices
        
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    print(f"Fetching current prices for {len(tickers)} stocks from Yahoo Finance...")
    for i, ticker in enumerate(tickers):
        if not ticker:
            continue
        yahoo_ticker = f"{ticker}.IS"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_ticker}"
        
        # Add a tiny delay between requests to avoid rate limits
        if i > 0:
            time.sleep(0.2)
            
        try:
            response = curl_requests.get(url, headers=headers, timeout=5, impersonate='chrome')
            if response.status_code == 200:
                data = response.json()
                meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
                price = meta.get("regularMarketPrice")
                if price is not None:
                    prices[ticker] = price
                    print(f"  {ticker}: {price} TL")
                else:
                    print(f"  {ticker}: Price field not found in Yahoo response.")
            else:
                print(f"  {ticker}: Yahoo returned status code {response.status_code}")
        except Exception as e:
            print(f"  {ticker}: Error fetching price: {e}")
            
    return prices

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

def calculate_potential_html(target, current):
    """Helper to calculate and format potential upside/downside HTML."""
    if not target or not current or current <= 0:
        return ""
    diff = ((target - current) / current) * 100
    if diff > 0:
        return f'<span style="font-weight: bold; color: #10b981; margin-left: 6px; font-size: 12.5px;">(+%{diff:.1f})</span>'
    elif diff < 0:
        return f'<span style="font-weight: bold; color: #ef4444; margin-left: 6px; font-size: 12.5px;">(-%{abs(diff):.1f})</span>'
    else:
        return f'<span style="font-weight: bold; color: #64748b; margin-left: 6px; font-size: 12.5px;">(%0.0)</span>'

def generate_html_report(recent_by_stock, all_by_stock, lookback_hours, prices_dict):
    """Generates a styled HTML email body with a summary table, current prices, potentials, and model portfolios."""
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
        max-width: 720px;
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
    .section-title {
        margin-top: 0; 
        color: #1e3a8a; 
        font-size: 16px; 
        font-weight: bold;
        border-bottom: 2px solid #e2e8f0; 
        padding-bottom: 8px;
        margin-bottom: 16px;
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
    .summary-table-style {
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 32px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 4px 6px rgba(0,0,0,0.02);
        border-radius: 8px;
        overflow: hidden;
    }
    .summary-table-style th {
        background-color: #1e3a8a;
        color: #ffffff;
        font-size: 12px;
        font-weight: 600;
        padding: 12px 16px;
        text-align: left;
        border: 1px solid #1e3a8a;
    }
    .summary-table-style td {
        padding: 12px 16px;
        font-size: 13px;
        border-bottom: 1px solid #e2e8f0;
        border-right: 1px solid #e2e8f0;
        vertical-align: middle;
        background-color: #ffffff;
    }
    .summary-table-style tr:nth-child(even) td {
        background-color: #f8fafc;
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
    
    # ------------------ SUMMARY TABLE ------------------
    if recent_by_stock:
        html_body += """
                    <div class="section-title">Günün Hedef Fiyat Güncellemeleri Özet Tablosu</div>
                    <table class="summary-table-style">
                        <thead>
                            <tr>
                                <th>Hisse Sembolü</th>
                                <th>Son Fiyat</th>
                                <th>Güncelleyen Kurum</th>
                                <th>Potansiyel Getiri / Fiyat / Ortalama Hedef Potansiyel</th>
                                <th style="text-align: center;">Toplam Kurum</th>
                            </tr>
                        </thead>
                        <tbody>
        """
        for stock, updates in recent_by_stock.items():
            all_ratings = all_by_stock.get(stock, [])
            current_price = prices_dict.get(stock)
            
            price_val_str = f"{current_price:.2f} TL" if current_price else "-"
            
            # List of updating brokers short titles
            updating_brokers = []
            for item in updates:
                broker_name = item.get('brokerage', {}).get('short_title') or item.get('brokerage', {}).get('title', '-')
                updating_brokers.append(broker_name)
            brokers_str = ", ".join(updating_brokers)
            
            # Generate potential targets descriptions
            targets_pot_list = []
            for item in updates:
                broker_short = item.get('brokerage', {}).get('short_title') or item.get('brokerage', {}).get('title', '-')
                target_val = item.get('price_target')
                pot_html = ""
                if target_val and current_price and current_price > 0:
                    diff = ((target_val - current_price) / current_price) * 100
                    pot_txt = f"+%{diff:.1f}" if diff > 0 else (f"-%{abs(diff):.1f}" if diff < 0 else "%0.0")
                    pot_html = f" ({pot_txt})"
                    
                target_txt = f"{target_val:.2f} TL" if target_val else "-"
                targets_pot_list.append(f"<strong>{broker_short}</strong>: {target_txt}{pot_html}")
                
            # Consensus Stats
            targets = [r.get('price_target') for r in all_ratings if r.get('price_target') is not None]
            avg_target = sum(targets) / len(targets) if targets else 0
            num_brokers = len(all_ratings)
            
            avg_target_str = f"{avg_target:.2f} TL" if avg_target else "-"
            avg_pot_html = ""
            if avg_target and current_price and current_price > 0:
                avg_diff = ((avg_target - current_price) / current_price) * 100
                avg_pot_txt = f"+%{avg_diff:.1f}" if avg_diff > 0 else (f"-%{abs(avg_diff):.1f}" if avg_diff < 0 else "%0.0")
                avg_pot_html = f" ({avg_pot_txt} Ort. Pot.)"
                
            targets_pot_list.append(f'<span style="color: #1e3a8a; font-size: 11.5px; border-top: 1px dashed #cbd5e1; display: block; margin-top: 4px; padding-top: 4px;">Konsensüs Ort: {avg_target_str}{avg_pot_html}</span>')
            
            pot_fiyat_avg_html = "<br>".join(targets_pot_list)
            
            html_body += f"""
                            <tr>
                                <td style="font-weight: bold; color: #1e3a8a; font-size: 14px;">{stock}</td>
                                <td style="font-weight: 600; color: #475569;">{price_val_str}</td>
                                <td>{brokers_str}</td>
                                <td>{pot_fiyat_avg_html}</td>
                                <td style="text-align: center; font-weight: 600;">{num_brokers}</td>
                            </tr>
            """
            
        html_body += """
                        </tbody>
                    </table>
        """
        
    # ------------------ MAIN CONTENT & DETAILED STOCK CARDS ------------------
    if not recent_by_stock:
        # If no updates, display an empty state report
        html_body += f"""
                    <div class="empty-state">
                        <h3>Son {lookback_hours} Saat İçinde Yeni Hedef Fiyat Bulunamadı</h3>
                        <p>Aracı kurumlar tarafından son 24 saat içerisinde herhangi bir hisse için yeni hedef fiyat veya tavsiye güncellemesi yayınlanmadı.</p>
                    </div>
        """
        
        # As an enhancement, show the last 10 updates overall in the system so the mail isn't empty
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
                                    <th>Tavsiye / Fark</th>
                                    <th>Tarih</th>
                                </tr>
                            </thead>
                            <tbody>
            """
            for item in all_updates[:10]:
                stock = item.get('code', '-')
                current_price = prices_dict.get(stock)
                broker = item.get('brokerage', {}).get('short_title') or item.get('brokerage', {}).get('title', '-')
                target = item.get('price_target')
                target_str = f"{target:.2f} TL" if target else "Belirtilmemiş"
                
                # Format price string next to stock name
                stock_label = stock
                if current_price:
                    stock_label = f'{stock} <br><span style="font-size: 11px; color: #64748b; font-weight: normal;">(Fiyat: {current_price:.2f} TL)</span>'
                    
                rec_type = item.get('type')
                rec_text = TYPE_TRANSLATIONS.get(rec_type, 'Belirtilmemiş')
                badge_color = TYPE_COLORS.get(rec_type, '#6b7280')
                
                # Potential Diff
                pot_html = calculate_potential_html(target, current_price)
                
                # Model portfolio badge
                mp_badge = ""
                if item.get('in_model_portfolio'):
                    mp_badge = '<span class="badge" style="background-color: #2563eb; margin-left: 4px; font-size: 9px; padding: 2px 5px; vertical-align: middle;">Model Portföy</span>'
                
                pub_str = item.get('published_at', '')
                pub_date = "-"
                if pub_str:
                    try:
                        pub_dt = datetime.fromisoformat(pub_str.replace('Z', '+00:00'))
                        pub_dt_tr = pub_dt.astimezone(timezone(timedelta(hours=3)))
                        pub_date = pub_dt_tr.strftime("%d.%m.%Y")
                    except Exception:
                        pub_date = pub_str[:10]
                        
                html_body += f"""
                                <tr>
                                    <td style="font-weight: 700; color: #1e3a8a; vertical-align: middle;">{stock_label}</td>
                                    <td>{broker}</td>
                                    <td style="font-weight: 600;">{target_str}</td>
                                    <td>
                                        <span class="badge" style="background-color: {badge_color};">{rec_text}</span>
                                        {pot_html}
                                        {mp_badge}
                                    </td>
                                    <td style="color: #64748b; font-size: 12px;">{pub_date}</td>
                                </tr>
                """
            html_body += """
                            </tbody>
                        </table>
                    </div>
            """
    else:
        html_body += """
                    <div class="section-title" style="margin-top: 32px;">Hisse Bazlı Detaylı Raporlar</div>
        """
        for stock, updates in recent_by_stock.items():
            all_ratings = all_by_stock.get(stock, [])
            current_price = prices_dict.get(stock)
            
            # Model portfolio status at stock level
            mp_brokers = [r.get('brokerage', {}).get('short_title') or r.get('brokerage', {}).get('title', '') 
                          for r in all_ratings if r.get('in_model_portfolio')]
            mp_html = ""
            if mp_brokers:
                mp_brokers_str = ", ".join(mp_brokers[:3])
                if len(mp_brokers) > 3:
                    mp_brokers_str += "..."
                mp_html = f'<span class="badge" style="background-color: #2563eb; font-size: 10px; margin-left: 8px; vertical-align: middle; padding: 3px 8px;">Model Portföy ({mp_brokers_str})</span>'
            
            # Format price string next to stock name
            price_str = ""
            if current_price:
                price_str = f'<span style="font-size: 14px; color: #64748b; font-weight: 600; margin-left: 8px; vertical-align: middle;">(Son Fiyat: {current_price:.2f} TL)</span>'
            
            # Calculate Consensus Stats
            targets = [r.get('price_target') for r in all_ratings if r.get('price_target') is not None]
            avg_target = sum(targets) / len(targets) if targets else 0
            min_target = min(targets) if targets else 0
            max_target = max(targets) if targets else 0
            num_brokers = len(all_ratings)
            
            avg_target_str = f"{avg_target:.2f} TL" if avg_target else "-"
            min_target_str = f"{min_target:.2f} TL" if min_target else "-"
            max_target_str = f"{max_target:.2f} TL" if max_target else "-"
            
            # Consensus average potential upside html
            avg_pot_html = ""
            if current_price and avg_target:
                avg_pot = ((avg_target - current_price) / current_price) * 100
                if avg_pot > 0:
                    avg_pot_html = f'<div style="font-size: 11px; color: #10b981; font-weight: 600; margin-top: 2px;">(+%{avg_pot:.1f} Pot.)</div>'
                elif avg_pot < 0:
                    avg_pot_html = f'<div style="font-size: 11px; color: #ef4444; font-weight: 600; margin-top: 2px;">(-%{abs(avg_pot):.1f} Pot.)</div>'
                else:
                    avg_pot_html = f'<div style="font-size: 11px; color: #64748b; font-weight: 600; margin-top: 2px;">(%0.0 Pot.)</div>'
            
            html_body += f"""
                    <div class="stock-card">
                        <div class="stock-header">
                            <span class="stock-ticker">{stock}</span>
                            {price_str}
                            {mp_html}
                            <span style="float: right; color: #64748b; font-size: 12px; margin-top: 6px;">Güncelleme Sayısı: {len(updates)}</span>
                            <div style="clear: both;"></div>
                        </div>
                        
                        <!-- Today's Updates Table -->
                        <table class="ratings-table">
                            <thead>
                                <tr>
                                    <th>Yeni Güncelleyen Aracı Kurum</th>
                                    <th>Yeni Hedef Fiyat</th>
                                    <th>Tavsiye / Fark</th>
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
                
                # Recommendation translation and color
                rec_type = item.get('type')
                rec_text = TYPE_TRANSLATIONS.get(rec_type, 'Belirtilmemiş')
                badge_color = TYPE_COLORS.get(rec_type, '#6b7280')
                
                # Percentage Difference
                pot_html = calculate_potential_html(target, current_price)
                
                # Model Portfolio
                mp_badge = ""
                if item.get('in_model_portfolio'):
                    mp_badge = '<span class="badge" style="background-color: #2563eb; margin-left: 4px; font-size: 10px; padding: 2px 6px; vertical-align: middle;">Model Portföy</span>'
                
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
                                    <td>
                                        <span class="badge" style="background-color: {badge_color};">{rec_text}</span>
                                        {pot_html}
                                        {mp_badge}
                                    </td>
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
                    
                    # Percentage Difference
                    pot_html = calculate_potential_html(target, current_price)
                    
                    # Model Portfolio
                    mp_badge = ""
                    if item.get('in_model_portfolio'):
                        mp_badge = '<span class="badge" style="background-color: #2563eb; margin-left: 4px; font-size: 9px; padding: 1px 4px; vertical-align: middle;">Model Portföy</span>'
                    
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
                                    <td style="padding: 8px 20px;">
                                        <span class="badge" style="background-color: {badge_color}; opacity: 0.85; padding: 2px 6px; font-size: 10px;">{rec_text}</span>
                                        {pot_html}
                                        {mp_badge}
                                    </td>
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
                                        {avg_pot_html}
                                    </td>
                                    <td class="stat-cell" style="border-left: 1px solid #e2e8f0; vertical-align: top;">
                                        <div class="stat-label">En Yüksek Hedef</div>
                                        <div class="stat-value">{max_target_str}</div>
                                    </td>
                                    <td class="stat-cell" style="border-left: 1px solid #e2e8f0; vertical-align: top;">
                                        <div class="stat-label">En Düşük Hedef</div>
                                        <div class="stat-value">{min_target_str}</div>
                                    </td>
                                    <td class="stat-cell" style="border-left: 1px solid #e2e8f0; vertical-align: top;">
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
    msg['Subject'] = Header(subject, 'utf-8')
    
    # Use clean From header to prevent Gmail spoofing/spam detection
    if EMAIL_FROM:
        msg['From'] = f"{Header(EMAIL_FROM, 'utf-8')} <{SMTP_USERNAME}>"
    else:
        msg['From'] = SMTP_USERNAME
        
    msg['To'] = ", ".join(recipients)
    
    # Crucial headers for spam filters (RFC 5322)
    smtp_domain = SMTP_SERVER.split('.')[-2] if '.' in SMTP_SERVER else 'gmail'
    msg['Message-ID'] = make_msgid(domain=f"{smtp_domain}.com")
    msg['Date'] = formatdate(localtime=True)
    msg['MIME-Version'] = '1.0'
    
    msg.attach(MIMEText(html_content, 'html', 'utf-8'))
    
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
        
        # Determine all tickers we need to fetch prices for
        tickers_to_fetch = list(recent_by_stock.keys())
        if not tickers_to_fetch:
            # If no recent updates, we'll show the last 10 updates, so fetch those stock tickers
            all_updates = []
            for stock, items in all_by_stock.items():
                all_updates.extend(items)
            all_updates.sort(key=lambda x: x.get('published_at', ''), reverse=True)
            tickers_to_fetch = list(set(item.get('code') for item in all_updates[:10] if item.get('code')))
            
        # Fetch current stock prices from Yahoo Finance
        prices_dict = fetch_current_prices(tickers_to_fetch)
        
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
            
        html_content = generate_html_report(recent_by_stock, all_by_stock, LOOKBACK_HOURS, prices_dict)
        
        send_email(subject, html_content)
        
    except Exception as e:
        print(f"Bot failed with error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
