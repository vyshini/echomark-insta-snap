"""
EchoMark - Instagram OSINT Tool
Fetches and cross-verifies profile data from HikerAPI + Instagram120 (RapidAPI).
"""

import os
import re
import json
import requests
from dotenv import load_dotenv
from datetime import datetime

# ---------- Setup ----------
load_dotenv()
API_KEY = os.environ.get("HIKERAPI_KEY")
RAPIDAPI_KEY = os.environ.get("FLASHAPI_KEY")
BASE_URL = "https://api.hikerapi.com"

HIKER_HEADERS = {"x-access-key": API_KEY, "accept": "application/json"}
RAPIDAPI_HEADERS = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": "instagram120.p.rapidapi.com",
    "Content-Type": "application/json"
}


# ---------- HikerAPI functions ----------
def get_profile(username):
    r = requests.get(f"{BASE_URL}/v1/user/by/username",
                      params={"username": username}, headers=HIKER_HEADERS)
    r.raise_for_status()
    return r.json()


def get_followers(user_id, max_pages=5):
    followers, max_id, page = [], None, 0
    while page < max_pages:
        r = requests.get(f"{BASE_URL}/v1/user/followers/chunk",
                          params={"user_id": user_id, "max_id": max_id},
                          headers=HIKER_HEADERS)
        r.raise_for_status()
        users, max_id = r.json()
        followers.extend(users)
        page += 1
        if not max_id:
            break
    return followers


def get_following(user_id, max_pages=5):
    following, max_id, page = [], None, 0
    while page < max_pages:
        r = requests.get(f"{BASE_URL}/v1/user/following/chunk",
                          params={"user_id": user_id, "max_id": max_id},
                          headers=HIKER_HEADERS)
        r.raise_for_status()
        users, max_id = r.json()
        following.extend(users)
        page += 1
        if not max_id:
            break
    return following


def get_mutual_accounts(user_id):
    """
    Fetch similar/mutual accounts from Flash API.
    Used for private accounts where followers/following are inaccessible.
    """
    r = requests.get(
        "https://flashapi1.p.rapidapi.com/ig/similar_accounts/",
        params={"id_user": user_id},
        headers=RAPIDAPI_HEADERS
    )
    r.raise_for_status()
    return r.json()

def get_medias(user_id):
    r = requests.get(f"{BASE_URL}/v1/user/medias",
                      params={"user_id": user_id}, headers=HIKER_HEADERS)
    r.raise_for_status()
    return r.json()


def get_user_about(user_id):
    """
    Fetch account creation date, country, and former usernames
    from HikerAPI GQL endpoint.
    """
    r = requests.get(
        f"{BASE_URL}/gql/user/about",
        params={"id": user_id},
        headers=HIKER_HEADERS
    )
    r.raise_for_status()
    return r.json()


def parse_flash_media(items):
    """
    Extract post URL, thumbnail, video URL (if reel), posted time and caption.
    """
    results = []
    seen_codes = set()

    for item in items:
        media = item.get("media", {})
        code = media.get("code", "")

        if not code or code in seen_codes:
            continue
        seen_codes.add(code)

        is_reel = media.get("product_type", "") == "clips"
        taken_at_ts = media.get("taken_at", 0)

        # thumbnail
        thumbnail_url = ""
        candidates = media.get("image_versions2", {}).get("candidates", [])
        if candidates:
            thumbnail_url = candidates[0].get("url", "")

        # video URL — only exists for reels
        video_url = ""
        if is_reel:
            video_versions = media.get("video_versions", [])
            if video_versions:
                video_url = video_versions[0].get("url", "")

        # caption
        caption_obj = media.get("caption")
        caption_text = ""
        if isinstance(caption_obj, dict):
            caption_text = caption_obj.get("text", "")

        results.append({
            "post_url": f"https://www.instagram.com/{'reel' if is_reel else 'p'}/{code}/",
            "thumbnail_url": thumbnail_url,
            "video_url": video_url,  # empty string for regular posts
            "posted_at": datetime.fromtimestamp(taken_at_ts).strftime("%d %b %Y, %I:%M %p") if taken_at_ts else "unknown",
            "caption": caption_text,
        })

    return results

# ---------- Fake account scoring ----------
def analyze_username_pattern(username):
    reasons = []
    score = 0
    num_count = sum(c.isdigit() for c in username)
    if num_count >= 4:
        score += 1
        reasons.append(f"Username contains {num_count} numbers (possible auto-generated)")
    if username.startswith("_") or username.endswith("_"):
        score += 0.5
        reasons.append("Username starts/ends with underscore")
    if len(username) > 20:
        score += 0.5
        reasons.append("Unusually long username")
    if re.search(r'[a-z]{1,3}\d{3,}', username.lower()):
        score += 1
        reasons.append("Username pattern looks auto-generated (letters + number block)")
    return score, reasons


def analyze_captions(captions):
    reasons = []
    score = 0
    if not captions:
        return score, reasons
    for cap in captions:
        hashtag_count = len(re.findall(r'#\w+', cap))
        if hashtag_count >= 15:
            score += 1
            reasons.append(f"Hashtag stuffing detected ({hashtag_count} hashtags in one post)")
            break
    if len(captions) > 2:
        unique_ratio = len(set(captions)) / len(captions)
        if unique_ratio < 0.6:
            score += 1.5
            reasons.append("Repeated/duplicate captions across posts")
    return score, reasons


def analyze_follower_quality(followers, following,total_followers = 0):
    """
    Deep analysis of follower and following lists.
    Returns score and detailed reasons.
    """
    reasons = []
    score = 0

    if not followers and not following:
        return score, reasons

    # ---- Follower analysis ----
    if followers:
        total = len(followers)
        no_pic = 0
        generated_username = 0
        private_count = 0
        verified_count = 0

        for f in followers:
            username = f.get("username", "")
            has_pic = f.get("profile_pic_url") not in [None, ""]
            is_private = f.get("is_private", False)
            is_verified = f.get("is_verified", False)

            # no profile picture
            if not has_pic:
                no_pic += 1

            # auto-generated username pattern
            num_count = sum(c.isdigit() for c in username)
            if num_count >= 4:
                generated_username += 1

            if is_private:
                private_count += 1

            if is_verified:
                verified_count += 1

        no_pic_ratio = no_pic / total
        generated_ratio = generated_username / total
        private_ratio = private_count / total
        verified_ratio = verified_count / total

        if no_pic_ratio > 0.5:
            score += 2
            reasons.append(
                f"{int(no_pic_ratio*100)}% of followers have no profile picture (bot signal)"
            )
        elif no_pic_ratio > 0.3:
            score += 1
            reasons.append(
                f"{int(no_pic_ratio*100)}% of followers have no profile picture"
            )

        if generated_ratio > 0.4:
            score += 2
            reasons.append(
                f"{int(generated_ratio*100)}% of followers have auto-generated usernames"
            )
        elif generated_ratio > 0.2:
            score += 1
            reasons.append(
                f"{int(generated_ratio*100)}% of followers have suspicious usernames"
            )

        if private_ratio > 0.8:
            score += 1
            reasons.append(
                f"{int(private_ratio*100)}% of followers are private accounts (unusual)"
            )

        if verified_ratio > 0.1:
            score -= 1  # reduce score — having verified followers = credible
            reasons.append(
                f"{int(verified_ratio*100)}% of followers are verified accounts (credibility signal)"
            )

    # ---- Following analysis ----
    if following:
        total_following = len(following)
        generated_following = 0

        for f in following:
            username = f.get("username", "")
            num_count = sum(c.isdigit() for c in username)
            if num_count >= 4:
                generated_following += 1

        gen_following_ratio = generated_following / total_following
        if gen_following_ratio > 0.4:
            score += 1.5
            reasons.append(
                f"{int(gen_following_ratio*100)}% of following accounts have bot-like usernames"
            )

    # ---- Follower/following overlap (mutual follow farm detection) ----
    if followers and following:
        follower_ids = set(f.get("pk", f.get("id", "")) for f in followers)
        following_ids = set(f.get("pk", f.get("id", "")) for f in following)
        overlap = follower_ids & following_ids
        overlap_ratio = len(overlap) / max(len(follower_ids), 1)

        if overlap_ratio > 0.7:
            score += 2
            reasons.append(
                f"{int(overlap_ratio*100)}% overlap between followers and following "
                f"({len(overlap)} accounts) — possible mutual follow farm"
            )
        elif overlap_ratio > 0.4:
            score += 1
            reasons.append(
                f"{int(overlap_ratio*100)}% overlap between followers and following"
            )
    confidence = get_sample_confidence(len(followers), total_followers)
    score = score * confidence

    if confidence < 0.5:
        reasons.append(
            f"Note: follower analysis based on {len(followers)} sampled "
            f"out of {total_followers} total — low sample confidence"
        )


    return score, reasons

def get_sample_confidence(sampled, total):
    """How much to trust signals based on sample size."""
    if total == 0:
        return 0
    ratio = sampled / total
    if ratio >= 0.5:
        return 1.0    # high confidence
    elif ratio >= 0.2:
        return 0.7    # medium confidence
    elif ratio >= 0.05:
        return 0.4    # low confidence
    else:
        return 0.2    # very low confidence — flag it


def analyze_caption_quality(captions):
    score = 0
    reasons = []

    if not captions:
        return score, reasons

    # average caption length
    avg_len = sum(len(c) for c in captions) / len(captions)
    if avg_len < 5:
        score += 1
        reasons.append("Captions are extremely short on average")

    # all caps posting (shouting/spam behavior)
    all_caps = sum(1 for c in captions if c.upper() == c and len(c) > 5)
    if all_caps / len(captions) > 0.5:
        score += 1
        reasons.append(f"{int(all_caps/len(captions)*100)}% of captions are all-caps")

    # excessive emoji usage (no real text)
    import unicodedata
    def is_emoji(char):
        return unicodedata.category(char) in ('So', 'Sm')

    emoji_heavy = 0
    for cap in captions:
        total_chars = len(cap.replace(" ", ""))
        if total_chars == 0:
            continue
        emoji_chars = sum(1 for c in cap if is_emoji(c))
        if total_chars > 0 and emoji_chars / total_chars > 0.5:
            emoji_heavy += 1

    if len(captions) > 0 and emoji_heavy / len(captions) > 0.5:
        score += 0.5
        reasons.append("Most captions are emoji-only (no real text content)")

    # keyword spam detection
    spam_keywords = ["follow", "followback", "follow4follow", "f4f",
                      "like4like", "l4l", "dm for promo", "link in bio",
                      "giveaway", "win free"]
    spam_hits = 0
    for cap in captions:
        if any(kw in cap.lower() for kw in spam_keywords):
            spam_hits += 1

    if spam_hits > 0:
        score += spam_hits * 0.5
        reasons.append(f"{spam_hits} captions contain spam/promotional keywords")

    return score, reasons

def analyze_posting_hours(medias):
    score = 0
    reasons = []

    hours = []
    for m in medias:
        taken_at = m.get("taken_at", "")
        if taken_at:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(taken_at.replace("Z", "+00:00"))
                hours.append(dt.hour)
            except:
                pass

    if len(hours) < 3:
        return score, reasons

    # all posts at same hour — robotic scheduling
    unique_hours = set(hours)
    if len(unique_hours) == 1:
        score += 1.5
        reasons.append(f"All posts published at exactly hour {hours[0]}:00 — robotic scheduling")
    elif len(unique_hours) <= 2:
        score += 1
        reasons.append("Posts clustered in very narrow time window")

    # posting only at 3-5am (bot hours)
    suspicious_hours = sum(1 for h in hours if 2 <= h <= 5)
    if suspicious_hours / len(hours) > 0.7:
        score += 1
        reasons.append("Majority of posts published between 2-5am (bot-typical hours)")

    return score, reasons
def fake_account_score(profile, medias=None, followers=None, following=None, captions=None):
    total_score = 0
    all_reasons = []

    medias = medias or []
    followers = followers or []
    following = following or []
    captions = captions or []

    followers_count = profile.get("follower_count", 0)
    following_count = profile.get("following_count", 0)
    post_count = profile.get("post_count", profile.get("media_count", 0))
    username = profile.get("username", "")

    # --- Basic profile signals ---
    if post_count == 0:
        total_score += 1.5
        all_reasons.append("Account has zero posts")
    if not profile.get("bio", profile.get("biography", "")).strip():
        total_score += 0.5
        all_reasons.append("Empty bio")
    if not profile.get("full_name", "").strip():
        total_score += 0.5
        all_reasons.append("No full name set")
    if not profile.get("profile_pic_url"):
        total_score += 1
        all_reasons.append("No profile picture set")

    # --- Follower/following ratio ---
    if followers_count > 0:
        ratio = following_count / max(followers_count, 1)
        if ratio > 10:
            total_score += 2
            all_reasons.append(f"Following/follower ratio extremely high ({ratio:.1f}x)")
        elif ratio > 5:
            total_score += 1
            all_reasons.append(f"Following far more than followers (ratio {ratio:.1f}x)")
    elif following_count > 500 and followers_count == 0:
        total_score += 2
        all_reasons.append("Following many accounts with zero followers")

    # --- Engagement rate (from raw medias if available) ---
    if medias and followers_count > 1000:
        avg_likes = sum(m.get("like_count", 0) for m in medias) / len(medias)
        eng_rate = avg_likes / followers_count
        if eng_rate < 0.001:
            total_score += 2
            all_reasons.append(f"Extremely low engagement ({eng_rate*100:.3f}%)")
        elif eng_rate < 0.005:
            total_score += 1
            all_reasons.append(f"Low engagement rate ({eng_rate*100:.2f}%)")

    # --- Username pattern ---
    u_score, u_reasons = analyze_username_pattern(username)
    total_score += u_score
    all_reasons.extend(u_reasons)

    # --- Caption analysis ---
    # use passed captions if available, otherwise extract from raw medias
    if not captions and medias:
        captions = [m.get("caption_text", "") for m in medias if m.get("caption_text")]

    c_score, c_reasons = analyze_captions(captions)
    total_score += c_score
    all_reasons.extend(c_reasons)

    cq_score, cq_reasons = analyze_caption_quality(captions)
    total_score += cq_score
    all_reasons.extend(cq_reasons)

    # --- Posting hours ---
    ph_score, ph_reasons = analyze_posting_hours(medias)
    total_score += ph_score
    all_reasons.extend(ph_reasons)

    # --- Follower/following quality ---
    # followers/following are now username strings — updated logic
    if followers or following:
        # bot-like username detection on follower names
        if followers:
            bot_like = sum(
                1 for uname in followers
                if isinstance(uname, str) and sum(c.isdigit() for c in uname) >= 4
            )
            ratio = bot_like / len(followers)
            if ratio > 0.4:
                total_score += 2
                all_reasons.append(
                    f"{int(ratio*100)}% of sampled followers have bot-like usernames"
                )
            elif ratio > 0.2:
                total_score += 1
                all_reasons.append(
                    f"{int(ratio*100)}% of sampled followers have suspicious usernames"
                )

        # bot-like username detection on following names
        if following:
            bot_following = sum(
                1 for uname in following
                if isinstance(uname, str) and sum(c.isdigit() for c in uname) >= 4
            )
            ratio_f = bot_following / len(following)
            if ratio_f > 0.4:
                total_score += 1.5
                all_reasons.append(
                    f"{int(ratio_f*100)}% of following accounts have bot-like usernames"
                )

        # overlap detection
        if followers and following:
            f_set = set(followers)
            fg_set = set(following)
            overlap = f_set & fg_set
            overlap_ratio = len(overlap) / max(len(f_set), 1)
            if len(f_set) >= 10:  # only flag if sample is meaningful
                if overlap_ratio > 0.7:
                    total_score += 2
                    all_reasons.append(
                        f"{int(overlap_ratio*100)}% overlap between followers and following "
                        f"({len(overlap)} accounts) — possible mutual follow farm"
                    )
                elif overlap_ratio > 0.4:
                    total_score += 1
                    all_reasons.append(
                        f"{int(overlap_ratio*100)}% overlap between followers and following"
                    )

        # sample confidence note
        if followers_count > 0:
            confidence = get_sample_confidence(len(followers), followers_count)
            if confidence < 0.5:
                all_reasons.append(
                    f"Note: follower analysis based on {len(followers)} sampled "
                    f"out of {followers_count} total — low confidence"
                )

    max_possible = 18
    normalized = min(max(round((total_score / max_possible) * 100), 0), 100)

    if normalized >= 70:
        label = "HIGH RISK — Likely fake or bot account"
    elif normalized >= 40:
        label = "MEDIUM RISK — Suspicious, needs further investigation"
    elif normalized >= 20:
        label = "LOW RISK — Some anomalies detected"
    else:
        label = "LIKELY GENUINE"

    return {
        "fake_score_percent": normalized,
        "verdict": label,
        "signals_triggered": len(all_reasons),
        "reasons": all_reasons,
    }

# ---------- Orchestration ----------
def investigate(username, deep=True, use_cache=True):
    cache_path = f"data/{username}.json"

    if use_cache and os.path.exists(cache_path):
        print(f"[+] Loading cached data for '{username}'...")
        with open(cache_path) as f:
            return json.load(f)

    print(f"\n[+] Fetching profile from HikerAPI...")
    hiker_profile = get_profile(username)
    user_id = hiker_profile["pk"]
    is_private = hiker_profile.get("is_private", False)

    # --- build result dict with all keys initialized upfront ---
    result = {
        "username": hiker_profile["username"],
        "user_id": user_id,
        "full_name": hiker_profile.get("full_name", ""),
        "bio": hiker_profile.get("biography", ""),
        "profile_pic_url": (
            hiker_profile.get("profile_pic_url_hd") or
            hiker_profile.get("profile_pic_url", "")
        ),
        "follower_count": hiker_profile.get("follower_count", 0),
        "following_count": hiker_profile.get("following_count", 0),
        "post_count": hiker_profile.get("media_count", 0),
        "is_private": is_private,
        "is_verified": hiker_profile.get("is_verified", False),
        "account_details": {
            "country": "",
            "joined_date": "",
            "former_usernames_count": "",
        },
        "captions": [],
        "media_urls": [],
        "followers": [],
        "following": [],
        "similar_accounts": [],
        "fake_account_analysis": None,
    }

    # --- fetch additional info (account creation date etc.) ---
    print("[+] Fetching additional account info...")
    try:
        about = get_user_about(user_id)
        result["account_details"] = {
        "country": about.get("country", ""),
        "joined_date": about.get("date", ""),
        "former_usernames_count": about.get("former_usernames", ""),
        "is_verified": about.get("is_verified", False),
    }
    except requests.exceptions.HTTPError as e:
        print(f"[!] Could not fetch additional info: {e}")

    medias = []

    if is_private:
        print("[!] Account is private — only public profile metadata available.")
        try:
            print("[+] Fetching similar accounts...")
            similar_raw = get_mutual_accounts(user_id)
            # extract only usernames, no duplicates
            result["similar_accounts"] = list(dict.fromkeys(
                item.get("username", "")
                for item in similar_raw
                if item.get("username")
            ))
            print(f"[+] mutual accounts fetched: {len(result['similar_accounts'])}")
        except requests.exceptions.HTTPError as e:
            print(f"[!] Could not fetch mutual accounts: {e}")
    elif deep:
        try:
            print("[+] Fetching posts...")
            medias = get_medias(user_id)


            # save permanent post/reel URLs + metadata
            result["media_urls"] = [
                {
                    "post_url": f"https://www.instagram.com/{'reel' if m.get('product_type') == 'clips' else 'p'}/{m.get('code', '')}/",
                    "thumbnail_url": (
                        m.get("thumbnail_url") or
                        (m["image_versions"][0].get("url", "")
                         if m.get("image_versions") and len(m["image_versions"]) > 0
                         else "")
                    ),
                    "video_url": m.get("video_url", ""),
                    "taken_at_ts": m.get("taken_at_ts", 0),
                    "caption": m.get("caption_text", ""),
                }
                for m in medias if m.get("code")
            ]

            # save only caption text (no duplicates)
            seen_captions = set()
            for m in medias:
                cap = m.get("caption_text", "").strip()
                if cap and cap not in seen_captions:
                    seen_captions.add(cap)
                    result["captions"].append(cap)

        except requests.exceptions.HTTPError as e:
            print(f"[!] Could not fetch posts: {e}")

        try:
            print("[+] Fetching followers...")
            raw_followers = get_followers(user_id)
            # save only usernames, no duplicates
            result["followers"] = list(dict.fromkeys(
                u.get("username") for u in raw_followers
                if u.get("username")
            ))
       
        except requests.exceptions.HTTPError as e:
            print(f"[!] Could not fetch followers: {e}")

        try:
            print("[+] Fetching following...")
            raw_following = get_following(user_id)
            # save only usernames, no duplicates
            result["following"] = list(dict.fromkeys(
                u.get("username") for u in raw_following
                if u.get("username")
            ))
        except requests.exceptions.HTTPError as e:
            print(f"[!] Could not fetch following: {e}")

    # --- fake account scoring ---
    result["fake_account_analysis"] = fake_account_score(
        result, medias, result["followers"], result["following"]
    )

    return result


def save_result(result, folder="data"):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{result['username']}.json")
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    return path


# ---------- Entry point ----------
if __name__ == "__main__":
    username = input("Enter target Instagram username: ").strip()
    deep_input = input("Full investigation (followers/following/posts)? (y/n): ").strip().lower()
    fresh_input = input("Force fresh data (ignore cache)? (y/n): ").strip().lower()

    deep = deep_input == "y"
    use_cache = fresh_input != "y"

    try:
        data = investigate(username, deep=deep, use_cache=use_cache)
        path = save_result(data)
        print(f"[+] Saved to: {path}")

    except requests.exceptions.HTTPError as e:
        print(f"[-] API error: {e}")
    except Exception as e:
        print(f"[-] Something went wrong: {e}")