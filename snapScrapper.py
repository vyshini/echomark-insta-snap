import os
import requests
import json
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# --- HELPER FUNCTIONS ---
def parse_numeric_str(val):
    """Safely converts string numbers (e.g., '2846', '2.8k') to integers."""
    if not val:
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    val_str = str(val).lower().replace(",", "").strip()
    try:
        if "k" in val_str:
            return int(float(val_str.replace("k", "")) * 1000)
        if "m" in val_str:
            return int(float(val_str.replace("m", "")) * 1000000)
        return int(float(val_str))
    except (ValueError, TypeError):
        return 0

def ms_to_date(ms_string):
    """Converts millisecond timestamp string to a readable date."""
    try:
        ms = int(ms_string)
        if ms == 0: return "Unknown"
        return datetime.fromtimestamp(ms / 1000.0).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return "Unknown"

# --- SCRAPER CLASS ---
class SnapchatScraper:
    def __init__(self, api_token):
        if not api_token:
            raise ValueError("API Token is missing! Please check your .env file.")
        self.api_token = api_token
        self.base_url = "https://ensembledata.com/apis"

    def get_user_info(self, username):
        endpoint = "/snapchat/user/info"
        params = {"name": username, "token": self.api_token}
        try:
            response = requests.get(self.base_url + endpoint, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ Error fetching data: {e}")
            return None

# --- DATA EXTRACTION ---
def extract_comprehensive_data(api_response, username, country_override="India"):
    """Extracts only the required fields. Keeps JSON clean."""
    if not api_response:
        return None
        
    root_data = api_response.get("data", {})
    public_profile = root_data.get("userProfile", {}).get("publicProfileInfo", {})
    viewer_info = root_data.get("viewerInfo", {})
    
    creation_ms = public_profile.get("creationTimestampMs", {}).get("value", "0")
    profile_country = country_override if country_override else viewer_info.get("country", "Unknown")
    
    profile_data = {
        "username": public_profile.get("username", username),
        "display_name": public_profile.get("title", username),
        "bio": public_profile.get("bio", ""),
        "follower_count": parse_numeric_str(public_profile.get("subscriberCount", "0")),
        "is_verified": public_profile.get("badge", 0) > 0,
        "country": profile_country,
        "account_creation_date": ms_to_date(creation_ms),
        "profile_picture_url": public_profile.get("profilePictureUrl", "")
    }
    
    spotlight_snaps = root_data.get("spotlightStoryMetadata", [])
    extracted_videos = []
    
    for snap in spotlight_snaps:
        video_meta = snap.get("videoMetadata", {})
        upload_ms = video_meta.get("uploadDateMs", "0")
        caption = snap.get("llmDescription") or video_meta.get("description") or ""
        
        extracted_videos.append({
            "video_url": video_meta.get("contentUrl", ""),
            "thumbnail_url": video_meta.get("thumbnailUrl", ""),
            "upload_date": ms_to_date(upload_ms),
            "caption": caption
        })
        
    return {
        "profile": profile_data,
        "videos": extracted_videos
    }

# --- BOT DETECTION ---
def detect_bot_activity(data, totals):
    """Analyzes data and returns a report. Does not modify raw data."""
    profile = data.get("profile", {})
    follower_count = profile.get("follower_count", 0)
    video_count = len(data.get("videos", []))
    
    total_views = totals.get("views", 0)
    total_likes = totals.get("likes", 0)
    total_comments = totals.get("comments", 0)
    total_shares = totals.get("shares", 0)
    
    print(f"DEBUG: Detector successfully received {total_views} views.")
    
    avg_views = (total_views / video_count) if video_count > 0 else 0
    total_interactions = total_likes + total_comments + total_shares
    engagement_rate = (total_interactions / total_views) if total_views > 0 else 0
    
    suspicion_score = 0
    reasons = []
    
    if total_views > 500 and engagement_rate < 0.005:
        suspicion_score += 40
        reasons.append(f"Low engagement rate: {engagement_rate:.4f}")
        
    if total_views > 1000 and total_comments == 0:
        suspicion_score += 30
        reasons.append("High views but zero comments (Likely artificial views)")
        
    if not profile.get("is_verified") and follower_count > 0 and avg_views > (follower_count * 10):
        suspicion_score += 20
        reasons.append("Views-to-follower ratio suggests viral botting")

    return {
        "is_suspicious": suspicion_score >= 50,
        "suspicion_score": suspicion_score,
        "flags": reasons,
        "metrics_snapshot": {
            "avg_views_per_video": int(avg_views),
            "calculated_engagement_rate": round(engagement_rate, 4)
        },
    }

# --- SAVING ---
def save_to_json_folder(data, username, folder_name="snapchatdata"):
    if not os.path.exists(folder_name):
        os.makedirs(folder_name)
        
    file_path = os.path.join(folder_name, f"{username}.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        
    print(f"💾 Data saved to: {file_path}")

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    TOKEN = os.getenv("ENSEMBLEDATA_TOKEN")
    
    try:
        scraper = SnapchatScraper(TOKEN)
        target_user = input("Enter the Snapchat username: ").strip()
        
        if target_user:
            raw_response = scraper.get_user_info(target_user)
            
            if raw_response:
                # 1. Clean Data Extraction (No totals saved here)
                data = extract_comprehensive_data(raw_response, target_user)
                
                # 2. Compute Totals In-Memory Safely
                spotlight_snaps = raw_response.get("data", {}).get("spotlightStoryMetadata", [])
                totals = {"views": 0, "likes": 0, "comments": 0, "shares": 0}
                
                for s in spotlight_snaps:
                    stats = s.get("engagementStats", {})
                    # Using parse_numeric_str safely strips strings and commas
                    totals["views"] += parse_numeric_str(stats.get("viewCount", "0"))
                    totals["likes"] += parse_numeric_str(stats.get("boostCount", "0"))
                    totals["comments"] += parse_numeric_str(stats.get("commentCount", "0"))
                    totals["shares"] += parse_numeric_str(stats.get("shareCount", "0"))
                
                # 3. Detect Bot Activity & Attach Report
                data["bot_detection_report"] = detect_bot_activity(data, totals)
                
                # 4. Save cleanly
                save_to_json_folder(data, target_user)
            else:
                print("❌ Could not retrieve data.")
                
    except Exception as e:
        print(f"❌ Error: {e}")