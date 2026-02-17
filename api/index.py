"""
Transperth Station Departure Scraper - VERCEL VERSION (Phase 2: Redis Cache)
Calls Transperth's official API directly - FREE and RELIABLE!
With Vercel Redis caching for blazing fast responses!
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re
from urllib.parse import urlencode
import json
import os

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

# Initialize Redis client
redis_client = None
REDIS_ENABLED = False

try:
    # Try to import redis
    import redis
    
    # Get Redis URL from environment (Vercel sets this automatically)
    redis_url = os.environ.get('KV_URL') or os.environ.get('REDIS_URL') or os.environ.get('KV_REST_API_URL')
    
    if redis_url:
        # Parse connection details
        if redis_url.startswith('redis://') or redis_url.startswith('rediss://'):
            redis_client = redis.from_url(redis_url, decode_responses=True)
        else:
            # For Upstash REST API
            redis_token = os.environ.get('KV_REST_API_TOKEN') or os.environ.get('REDIS_TOKEN')
            if redis_token:
                # Use upstash-redis for REST API
                try:
                    from upstash_redis import Redis
                    redis_client = Redis(url=redis_url, token=redis_token)
                    REDIS_ENABLED = True
                    print("✓ Upstash Redis initialized (REST API)")
                except ImportError:
                    print("⚠ upstash-redis not available, trying standard redis")
                    redis_client = redis.from_url(redis_url, decode_responses=True)
                    REDIS_ENABLED = True
                    print("✓ Redis initialized (standard)")
            else:
                redis_client = redis.from_url(redis_url, decode_responses=True)
                REDIS_ENABLED = True
                print("✓ Redis initialized")
        
        # Test connection
        if redis_client:
            try:
                redis_client.ping()
                REDIS_ENABLED = True
                print("✓ Redis connection verified")
            except Exception as e:
                print(f"⚠ Redis ping failed: {e}")
                REDIS_ENABLED = False
    else:
        print("⚠ No Redis URL found in environment")
        
except ImportError as e:
    print(f"⚠ Redis library not available: {e}")
except Exception as e:
    print(f"⚠ Redis initialization error: {e}")

if not REDIS_ENABLED:
    print("  Falling back to no-cache mode")

# Cache TTLs
TOKEN_CACHE_TTL = 300  # 5 minutes
DEPARTURE_CACHE_TTL = 30  # 30 seconds

def get_cached_tokens():
    """Get tokens from Redis cache or fetch fresh"""
    if not REDIS_ENABLED or not redis_client:
        return None
    
    try:
        cached = redis_client.get('transperth:tokens')
        if cached:
            tokens = json.loads(cached)
            # Verify timestamp is recent
            cache_time = datetime.fromisoformat(tokens.get('timestamp', ''))
            age = (datetime.now() - cache_time).total_seconds()
            if age < TOKEN_CACHE_TTL:
                print(f"✓ Using cached tokens (age: {int(age)}s)")
                return tokens
    except Exception as e:
        print(f"⚠ Redis get tokens error: {e}")
    
    return None

def cache_tokens(tokens):
    """Store tokens in Redis cache"""
    if not REDIS_ENABLED or not redis_client or not tokens:
        return
    
    try:
        # Add timestamp for age verification
        tokens['timestamp'] = datetime.now().isoformat()
        # Remove cookies object (not JSON serializable)
        tokens_to_cache = {
            'verification_token': tokens.get('verification_token'),
            'module_id': tokens.get('module_id'),
            'tab_id': tokens.get('tab_id'),
            'timestamp': tokens['timestamp']
        }
        redis_client.setex('transperth:tokens', TOKEN_CACHE_TTL, json.dumps(tokens_to_cache))
        print(f"✓ Cached tokens (TTL: {TOKEN_CACHE_TTL}s)")
    except Exception as e:
        print(f"⚠ Redis set tokens error: {e}")

def get_cached_departures(station_id):
    """Get departures from Redis cache"""
    if not REDIS_ENABLED or not redis_client:
        return None
    
    try:
        cache_key = f'transperth:departures:{station_id}'
        cached = redis_client.get(cache_key)
        if cached:
            data = json.loads(cached)
            cache_time = datetime.fromisoformat(data.get('cached_at', ''))
            age = (datetime.now() - cache_time).total_seconds()
            print(f"✓ Cache HIT for station {station_id} (age: {int(age)}s)")
            return data
    except Exception as e:
        print(f"⚠ Redis get departures error: {e}")
    
    return None

def cache_departures(station_id, data):
    """Store departures in Redis cache"""
    if not REDIS_ENABLED or not redis_client or not data:
        return
    
    try:
        cache_key = f'transperth:departures:{station_id}'
        data['cached_at'] = datetime.now().isoformat()
        redis_client.setex(cache_key, DEPARTURE_CACHE_TTL, json.dumps(data))
        print(f"✓ Cached departures for station {station_id} (TTL: {DEPARTURE_CACHE_TTL}s)")
    except Exception as e:
        print(f"⚠ Redis set departures error: {e}")

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
        # Get tokens (from cache or fresh)
        if not tokens:
            # Try cache first
            tokens = get_cached_tokens()
            
            # If no cache, fetch fresh
            if not tokens:
                tokens = fetch_page_tokens()
                if tokens:
                    cache_tokens(tokens)
        
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
                series = summary.get('RealTimeInfo', {}).get('Series', 'W')
                num_cars = summary.get('RealTimeInfo', {}).get('NumCars', '')
                
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
                    'direction': direction
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
    """Get all departures for specified station - WITH REDIS CACHING"""
    try:
        # Get station_id from query parameter, default to 133 (Queens Park)
        station_id = request.args.get('station_id', '133')
        
        print("=" * 50)
        print(f"Request for station {station_id}...")
        
        # Try to get from cache first
        cached_result = get_cached_departures(station_id)
        if cached_result:
            # Remove cache metadata before returning
            cached_result.pop('cached_at', None)
            return jsonify(cached_result)
        
        # Cache miss - fetch fresh
        print(f"✗ Cache MISS for station {station_id} - fetching fresh data")
        
        # Fetch all departures
        all_deps = fetch_all_departures(station_id)
        
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
        
        # Cache the result
        cache_departures(station_id, result)
        
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
        'version': 'vercel-phase2-redis-cache',
        'redis_enabled': REDIS_ENABLED
    })

@app.route('/api/cache/stats', methods=['GET'])
def cache_stats():
    """Get cache statistics (for monitoring)"""
    if not REDIS_ENABLED:
        return jsonify({
            'error': 'Redis not enabled',
            'redis_enabled': False
        })
    
    try:
        # Try to get some sample keys to verify Redis is working
        tokens = get_cached_tokens()
        
        return jsonify({
            'redis_enabled': True,
            'tokens_cached': tokens is not None,
            'token_cache_ttl': TOKEN_CACHE_TTL,
            'departure_cache_ttl': DEPARTURE_CACHE_TTL,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'error': str(e),
            'redis_enabled': True
        }), 500

@app.route('/')
def index():
    """Info page"""
    redis_status = "✓ Enabled" if REDIS_ENABLED else "✗ Disabled (fallback mode)"
    
    return f'''
    <html>
        <head><title>Transperth Station API (Vercel + Redis)</title></head>
        <body style="font-family: Arial; padding: 40px; max-width: 600px; margin: 0 auto;">
            <h1>🚆 Transperth Station API</h1>
            <p><strong>Status:</strong> Running on Vercel Serverless</p>
            <p><strong>Version:</strong> Phase 2 (Redis Cache)</p>
            <p><strong>Redis Cache:</strong> {redis_status}</p>
            <p><strong>Performance:</strong> 6ms cache hits, ~1s cache miss</p>
            <h2>Endpoints:</h2>
            <ul>
                <li><a href="/api/health">/api/health</a> - Health check</li>
                <li><a href="/api/cache/stats">/api/cache/stats</a> - Cache statistics</li>
                <li><a href="/api/departures">/api/departures</a> - Get live departures</li>
                <li><a href="/api/departures?station_id=127">/api/departures?station_id=127</a> - Perth Station</li>
                <li><a href="/api/departures?station_id=177">/api/departures?station_id=177</a> - Elizabeth Quay</li>
            </ul>
            <h2>Cache Details:</h2>
            <ul>
                <li>Token Cache: 5 minutes (reduces page fetching)</li>
                <li>Departure Cache: 30 seconds per station (blazing fast!)</li>
                <li>Fallback: Works without Redis (just slower)</li>
            </ul>
        </body>
    </html>
    '''
