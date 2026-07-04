import os
import sys
import json
import time
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
BASE_URL    = "https://api.apify.com/v2"
ACTOR_ID    = "harvestapi~linkedin-profile-scraper"
OUTPUT_DIR  = "data"
TIMEOUT     = 300   

def _is_empty(value):
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def clean(obj):
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            cv = clean(v)
            if not _is_empty(cv):
                cleaned[k] = cv
        return cleaned

    if isinstance(obj, list):
        cleaned_list = [clean(item) for item in obj]
        cleaned_list = [item for item in cleaned_list if not _is_empty(item)]
        return cleaned_list

    return obj

def _save_json(data, filepath: str):
    os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"    [saved] {filepath}")


def run_actor_sync(actor_id: str, actor_input: dict, token: str) -> list:
    url = f"{BASE_URL}/acts/{actor_id}/run-sync-get-dataset-items?token={token}"
    r = requests.post(
        url,
        json=actor_input,
        headers={"Content-Type": "application/json"},
        timeout=TIMEOUT
    )

    if not r.ok:
        print(f"    [!] Actor failed: {r.text[:400]}")
        r.raise_for_status()

    items = r.json()
    items = items if isinstance(items, list) else []
    return items

def _parse_profile(raw: dict) -> dict:
    return {
        "full_name":        (
                                (raw.get("firstName", "") + " " + raw.get("lastName", "")).strip()
                                or raw.get("fullName") or raw.get("name")
                            ),
        "first_name":       raw.get("firstName"),
        "last_name":        raw.get("lastName"),
        "headline":         raw.get("headline") or raw.get("title"),
        "summary":          raw.get("summary") or raw.get("about") or raw.get("description"),
        "location":         raw.get("location") or raw.get("geoLocation") or raw.get("city"),
        "country":          raw.get("country") or raw.get("countryCode"),
        "linkedin_url":     raw.get("linkedinUrl") or raw.get("url") or raw.get("profileUrl"),
        "public_id":        raw.get("publicId") or raw.get("username") or raw.get("publicIdentifier"),
        "profile_pic_url":  raw.get("photoUrl") or raw.get("profilePicUrl") or raw.get("picture"),
        "background_url":   raw.get("backgroundUrl") or raw.get("backgroundCoverImageUrl"),
        "followers":        raw.get("followersCount") or raw.get("followerCount"),
        "connections":      raw.get("connectionsCount") or raw.get("connectionCount"),
        "is_open_to_work":  raw.get("openToWork") or raw.get("isOpenToWork"),
        "is_premium":       raw.get("premium") or raw.get("isPremium") or raw.get("hasPremium"),
        "email":            raw.get("email") or (raw.get("emails") or [None])[0],
        "emails":           raw.get("emails") or [],
        "phone":            raw.get("phone") or raw.get("phoneNumber"),
        "websites":         raw.get("websites") or raw.get("urls") or [],
        "twitter":          raw.get("twitter") or raw.get("twitterHandle"),
    }


def _parse_experience(raw_list: list) -> list:
    result = []
    for e in (raw_list or []):
        if not isinstance(e, dict):
            continue
        result.append({
            "title":           e.get("title") or e.get("role"),
            "company":         e.get("companyName") or e.get("company") or e.get("name"),
            "company_url":     e.get("companyUrl") or e.get("companyLinkedinUrl") or e.get("linkedinUrl"),
            "company_logo":    e.get("companyLogo") or e.get("logo"),
            "location":        e.get("location") or e.get("locationName"),
            "start_date":      e.get("startDate") or e.get("start"),
            "end_date":        e.get("endDate") or e.get("end"),
            "is_current":      e.get("isCurrent") or e.get("current") or not e.get("endDate"),
            "duration":        e.get("duration") or e.get("durationShort"),
            "description":     e.get("description") or e.get("summary"),
            "employment_type": e.get("employmentType") or e.get("type"),
        })
    return result


def _parse_education(raw_list: list) -> list:
    result = []
    for e in (raw_list or []):
        if not isinstance(e, dict):
            continue
        result.append({
            "school":       e.get("schoolName") or e.get("school") or e.get("name"),
            "school_url":   e.get("schoolUrl") or e.get("linkedinUrl"),
            "school_logo":  e.get("schoolLogo") or e.get("logo"),
            "degree":       e.get("degreeName") or e.get("degree"),
            "field":        e.get("fieldOfStudy") or e.get("field"),
            "start_date":   e.get("startDate") or e.get("start"),
            "end_date":     e.get("endDate") or e.get("end"),
            "grade":        e.get("grade"),
            "activities":   e.get("activities") or e.get("activitiesAndSocieties"),
            "description":  e.get("description"),
        })
    return result


def _parse_skills(raw) -> list:
    result = []
    for s in (raw or []):
        if isinstance(s, str):
            result.append({"name": s, "endorsements": None})
        elif isinstance(s, dict):
            result.append({
                "name":         s.get("name") or s.get("skill"),
                "endorsements": s.get("endorsementsCount") or s.get("endorsements"),
            })
    return result


def _parse_certifications(raw_list: list) -> list:
    result = []
    for c in (raw_list or []):
        if not isinstance(c, dict):
            continue
        result.append({
            "name":         c.get("name") or c.get("title"),
            "issuer":       c.get("authority") or c.get("issuer") or c.get("issuingOrganization"),
            "issued_at":    c.get("timePeriod", {}).get("start", {}) if isinstance(c.get("timePeriod"), dict) else c.get("issuedAt"),
            "expires_at":   c.get("timePeriod", {}).get("end", {}) if isinstance(c.get("timePeriod"), dict) else c.get("expiresAt"),
            "credential_id": c.get("licenseNumber") or c.get("credentialId"),
            "url":          c.get("url"),
        })
    return result


def _parse_recommendations(raw_list: list) -> list:
    result = []
    for r in (raw_list or []):
        if not isinstance(r, dict):
            continue
        result.append({
            "author_name":  r.get("recommenderName") or r.get("authorName") or r.get("name"),
            "author_url":   r.get("recommenderUrl") or r.get("authorUrl") or r.get("url"),
            "relationship": r.get("relationship"),
            "text":         r.get("text") or r.get("description") or r.get("content"),
        })
    return result

def collect(linkedin_url: str, token: str, output_dir: str = OUTPUT_DIR) -> dict:
    linkedin_url = linkedin_url.rstrip("/")
    handle = (
        linkedin_url.split("/in/")[-1].split("/")[0]
        if "/in/" in linkedin_url
        else linkedin_url.split("/")[-1]
    )
    actor_input = {
        "urls": [linkedin_url],                    # ← correct field name (not profileUrls)
        "profileScraperMode": "Profile details no email ($4 per 1k)"
    }

    try:
        items = run_actor_sync(ACTOR_ID, actor_input, token)
    except requests.exceptions.Timeout:
        print("    [!] Request timed out — try again or increase TIMEOUT")
        items = []
    except Exception as e:
        print(f"    [!] Actor run failed: {e}")
        items = []

    raw = items[0] if items else {}

    if not raw:
        print("\n    [!] No data returned from actor.")
        print("    Possible reasons:")
        print("    1. Profile is private or doesn't exist at this URL")
        print("    2. Your Apify free credits ran out")
        print("    3. Try the profile URL directly in Apify console to verify")

    profile        = _parse_profile(raw)
    experience     = _parse_experience(raw.get("positions") or raw.get("experience") or raw.get("experiences") or [])
    education      = _parse_education(raw.get("education") or raw.get("schools") or raw.get("educations") or [])
    skills         = _parse_skills(raw.get("skills") or [])
    certifications = _parse_certifications(raw.get("certifications") or raw.get("licenses") or [])
    recommendations= _parse_recommendations(raw.get("recommendations") or [])

    result = {
        "meta": {
            "seed_url":     linkedin_url,
            "handle":       handle,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "source":       "Apify — harvestapi/linkedin-profile-scraper",
            "actor_id":     ACTOR_ID,
        },

        # ── Identity
        "profile": {
            "full_name":       profile.get("full_name"),
            "first_name":      profile.get("first_name"),
            "last_name":       profile.get("last_name"),
            "headline":        profile.get("headline"),
            "summary":         profile.get("summary"),
            "location":        profile.get("location"),
            "country":         profile.get("country"),
            "linkedin_url":    profile.get("linkedin_url"),
            "public_id":       profile.get("public_id"),
            "profile_pic_url": profile.get("profile_pic_url"),
            "background_url":  profile.get("background_url"),
            "followers":       profile.get("followers"),
            "connections":     profile.get("connections"),
            "is_open_to_work": profile.get("is_open_to_work"),
            "is_premium":      profile.get("is_premium"),
            "email":           profile.get("email"),
            "emails":          profile.get("emails"),
            "phone":           profile.get("phone"),
            "websites":        profile.get("websites"),
            "twitter":         profile.get("twitter"),
        },

        # ── Professional history
        "experience":       experience,
        "education":        education,
        "skills":           skills,
        "certifications":   certifications,
        "languages":        raw.get("languages") or [],
        "volunteer":        raw.get("volunteerExperiences") or raw.get("volunteer") or [],
        "honors":           raw.get("honors") or raw.get("awards") or [],
        "publications":     raw.get("publications") or [],
        "patents":          raw.get("patents") or [],
        "courses":          raw.get("courses") or [],
        "projects":         raw.get("projects") or [],

        # ── Social signals
        "recommendations":  recommendations,
        "accomplishments":  raw.get("accomplishments") or {},
        "interests":        raw.get("interests") or [],
        "groups":           raw.get("groups") or [],
        "activity":         raw.get("activity") or [],
        "featured":         raw.get("featured") or [],

        # ── Summary
        "summary": {
            "name":             profile.get("full_name"),
            "handle":           handle,
            "headline":         profile.get("headline"),
            "location":         profile.get("location"),
            "email":            profile.get("email"),
            "followers":        profile.get("followers"),
            "connections":      profile.get("connections"),
            "is_open_to_work":  profile.get("is_open_to_work"),
            "is_premium":       profile.get("is_premium"),
            "experience_count": len(experience),
            "education_count":  len(education),
            "skills_count":     len(skills),
            "certifications":   len(certifications),
            "languages":        len(raw.get("languages") or []),
            "recommendations":  len(recommendations),
            "groups":           len(raw.get("groups") or []),
            "data_collected":   bool(raw),
        },
    }

    result_clean = clean(result)  # strips null/""/[]/{} recursively

    os.makedirs(output_dir, exist_ok=True)
    safe_handle = "".join(c if c.isalnum() or c in "-_" else "_" for c in handle)
    main_path   = os.path.join(output_dir, f"linkedin_{safe_handle}.json")
    raw_path    = os.path.join(output_dir, f"linkedin_{safe_handle}_raw.json")

    _save_json(result_clean, main_path)   

    return result_clean

def main():
    token = APIFY_TOKEN

    print("\nEnter a LinkedIn profile URL :")

    user_input = input("URL : ").strip()
    if not user_input:
        sys.exit("\nERROR: No input provided.\n")

    linkedin_url = (
        user_input if user_input.startswith("http")
        else f"https://www.linkedin.com/in/{user_input.strip('/').split('/')[-1]}/"
    )
    print(f"\nResolved URL: {linkedin_url}")

    try:
        collect(linkedin_url, token, OUTPUT_DIR)
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.")
    except Exception as e:
        sys.exit(f"\nUnexpected error: {e}\n")


if __name__ == "__main__":
    main()