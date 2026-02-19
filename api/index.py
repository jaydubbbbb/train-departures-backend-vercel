"""
Transperth Station Departure Scraper - VERCEL VERSION (Phase 1: No Cache)
Calls Transperth's official API directly - FREE and RELIABLE!
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re
from urllib.parse import urlencode

app = Flask(__name__)
CORS(app)

# Perth timezone (UTC+8)
try:
    from zoneinfo import ZoneInfo
    PERTH_TZ = ZoneInfo('Australia/Perth')
except ImportError:
    # Fallback for older Python
    from datetime import timezone, timedelta
    PERTH_TZ = timezone(timedelta(hours=8))

# Transperth URLs
LIVE_TIMES_URL = "https://www.transperth.wa.gov.au/Timetables/Live-Train-Times"
API_URL = "https://www.transperth.wa.gov.au/API/SilverRailRestService/SilverRailService/GetStopTimetable"

def fetch_page_tokens():
    """Fetch the verification token and other required values from the page"""
    try:
        print("Fetching page tokens...")
        session = requests.Session()
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }
        
        response = session.get(LIVE_TIMES_URL, headers=headers, timeout=10)
        
        if response.status_code != 200:
            print(f"Failed to fetch page: {response.status_code}")
            return None
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find RequestVerificationToken (usually in a hidden input or meta tag)
        token_input = soup.find('input', {'name': '__RequestVerificationToken'})
        if token_input:
            verification_token = token_input.get('value')
        else:
            # Try meta tag
            token_meta = soup.find('meta', {'name': '__RequestVerificationToken'})
            verification_token = token_meta.get('content') if token_meta else None
        
        # Find ModuleId and TabId (often in script or data attributes)
        module_id = '5111'  # From your headers
        tab_id = '248'      # From your headers
        
        if verification_token:
            print(f"✓ Got verification token: {verification_token[:20]}...")
            return {
                'verification_token': verification_token,
                'module_id': module_id,
                'tab_id': tab_id,
                'cookies': session.cookies,
                'timestamp': datetime.now()
            }
        else:
            print("✗ Could not find verification token")
            return None
            
    except Exception as e:
        print(f"Error fetching tokens: {e}")
        return None

def calculate_minutes_until(depart_time_str):
    """Calculate minutes until departure from ISO format time"""
    try:
        # Parse the departure time (it's in Perth timezone)
        depart_time = datetime.fromisoformat(depart_time_str)
        
        # If the departure time doesn't have timezone info, assume it's Perth time
        if depart_time.tzinfo is None:
            depart_time = depart_time.replace(tzinfo=PERTH_TZ)
        
        # Get current time in Perth timezone
        now = datetime.now(PERTH_TZ)
        
        # Calculate difference
        diff = (depart_time - now).total_seconds() / 60
        return max(0, int(diff))
    except Exception as e:
        print(f"Error calculating time: {e}")
        return None

def fetch_all_departures(station_id='133', tokens=None):
    """Fetch all departures for specified station"""
    try:
        # Get fresh tokens if not provided
        if not tokens:
            tokens = fetch_page_tokens()
        
        if not tokens or not tokens.get('verification_token'):
            print("No verification token available")
            return []
        
        # Get current date/time
        now = datetime.now()
        search_date = now.strftime('%Y-%m-%d')
        search_time = now.strftime('%H:%M')
        
        # Prepare form data (application/x-www-form-urlencoded)
        form_data = {
            'StationId': station_id,
            'SearchDate': search_date,
            'SearchTime': search_time,
            'IsRealTimeChecked': 'true'
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en,zh-CN;q=0.9,zh;q=0.8',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': 'https://www.transperth.wa.gov.au',
            'Referer': LIVE_TIMES_URL,
            'X-Requested-With': 'XMLHttpRequest',
            'Requestverificationtoken': tokens['verification_token'],
            'Moduleid': tokens['module_id'],
            'Tabid': tokens['tab_id']
        }
        
        print(f"Fetching from API for station {station_id} at {search_time}...")
        response = requests.post(
            API_URL,
            data=urlencode(form_data),
            headers=headers,
            cookies=tokens.get('cookies'),
            timeout=10
        )
        
        if response.status_code != 200:
            print(f"API returned status {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return []
        
        # Debug: Print response
        print(f"API response status: {response.status_code}")
        print(f"Response content (first 500 chars): {response.text[:500]}")
        
        data = response.json()
        
        if data.get('result') != 'success':
            print(f"API result not success: {data.get('result')}")
            print(f"Full response: {data}")
            return []
        
        trips = data.get('trips', [])
        print(f"Found {len(trips)} trips for station {station_id}")
        
        departures = []
        
        for trip in trips:
            try:
                # Extract platform number from stop name
                stop_name = trip.get('StopTimetableStop', {}).get('Name', '')
                platform_match = re.search(r'Platform\s+(\d+)', stop_name)
                platform = platform_match.group(1) if platform_match else '?'
                
                # Get destination
                summary = trip.get('Summary', {})
                headsign = summary.get('Headsign', '')
                direction = summary.get('Direction', '0')  # 0 = To Perth, 1 = From Perth
                
                # Get display info
                display_title = trip.get('DisplayTripTitle', '')
                display_description = trip.get('DisplayTripDescription', '')
                display_status = trip.get('DisplayTripStatus', '')
                countdown = trip.get('DisplayTripStatusCountDown', '')
                
                # Get route info
                route_name = summary.get('RouteName', '')
                display_route_code = trip.get('DisplayRouteCode', '')
                
                # Get real-time info
                real_time = trip.get('RealTimeInfo', {})
                summary_real_time = summary.get('RealTimeInfo', {})
                series = summary_real_time.get('Series', 'W')
                num_cars = summary_real_time.get('NumCars', '')
                fleet_number = summary_real_time.get('FleetNumber', '')
                
                # Get scheduled and estimated times
                scheduled_time = trip.get('DepartTime', '')
                estimated_time = real_time.get('EstimatedDepartureTime', '')
                
                # Use estimated time if available, otherwise use scheduled
                # Convert estimated time format (HH:MM:SS) to full ISO format if needed
                if estimated_time:
                    # If estimated time is just time (no date), add the date from scheduled time
                    if 'T' not in estimated_time:
                        date_part = scheduled_time.split('T')[0] if 'T' in scheduled_time else datetime.now().strftime('%Y-%m-%d')
                        depart_time = f"{date_part}T{estimated_time}"
                    else:
                        depart_time = estimated_time
                else:
                    depart_time = scheduled_time
                
                # Calculate minutes until departure (using estimated or scheduled)
                minutes = calculate_minutes_until(depart_time)
                
                if minutes is None:
                    continue
                
                # Build stops description
                stops = f"All Stations"
                if num_cars:
                    stops = f"{stops} ({num_cars} cars)"
                if series:
                    stops = f"{stops} - {series} series"
                
                # Get delay/status information for logging
                delay_status = trip.get('RealTimeStopStatusDetail', '')
                
                departures.append({
                    'platform': platform,
                    'destination': display_title or headsign,
                    'time_display': countdown or display_status,
                    'minutes': minutes,
                    'pattern': series or 'W',
                    'stops': stops,
                    'route': route_name,
                    'route_code': display_route_code,
                    'direction': direction,
                    'fleetNumber': fleet_number
                })
                
                delay_info = f" ({delay_status})" if delay_status else ""
                print(f"  ✓ {display_title or headsign} in {minutes} min from platform {platform}{delay_info}")
                
            except Exception as e:
                print(f"Error parsing trip: {e}")
                continue
        
        return departures
        
    except Exception as e:
        print(f"Error fetching from API: {e}")
        import traceback
        traceback.print_exc()
        return []

@app.route('/api/departures', methods=['GET'])
def get_departures():
    """Get all departures for specified station - NO CACHING (serverless)"""
    try:
        # Get station_id from query parameter, default to 133 (Queens Park)
        station_id = request.args.get('station_id', '133')
        
        print("=" * 50)
        print(f"Fetching departures for station {station_id}...")
        
        # Fetch tokens fresh every time (no cache in serverless)
        tokens = fetch_page_tokens()
        
        if not tokens:
            return jsonify({
                'success': False,
                'error': 'Failed to fetch tokens from Transperth'
            }), 500
        
        # Fetch all departures
        all_deps = fetch_all_departures(station_id, tokens)
        
        print(f"\nTotal departures: {len(all_deps)}")
        
        # Separate by direction (0 = To Perth, 1 = From Perth)
        perth = [d for d in all_deps if d.get('direction') == '0']
        south = [d for d in all_deps if d.get('direction') == '1']
        
        perth.sort(key=lambda x: x['minutes'])
        south.sort(key=lambda x: x['minutes'])
        
        result = {
            'success': True,
            'perth': perth[:10],
            'south': south[:10],
            'station_id': station_id,
            'last_updated': datetime.now().isoformat()
        }
        
        return jsonify(result)
        
    except Exception as e:
        print(f"Error in get_departures: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'version': 'vercel-phase1-nocache'
    })

@app.route('/')
def index():
    """Info page"""
    return '''
    <html>
        <head><title>Transperth Station API (Vercel)</title></head>
        <body style="font-family: Arial; padding: 40px; max-width: 600px; margin: 0 auto;">
            <h1>🚆 Transperth Station API</h1>
            <p><strong>Status:</strong> Running on Vercel Serverless</p>
            <p><strong>Version:</strong> Phase 1 (No Cache)</p>
            <p><strong>Free:</strong> No API keys or external services needed!</p>
            <h2>Endpoints:</h2>
            <ul>
                <li><a href="/api/health">/api/health</a> - Health check</li>
                <li><a href="/api/departures">/api/departures</a> - Get live departures</li>
                <li><a href="/api/departures?station_id=127">/api/departures?station_id=127</a> - Perth Station</li>
                <li><a href="/api/departures?station_id=177">/api/departures?station_id=177</a> - Elizabeth Quay</li>
            </ul>
        </body>
    </html>
    '''
