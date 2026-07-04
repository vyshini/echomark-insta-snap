import os
import re
import sys
import json
import requests
from collections import Counter, defaultdict
from dotenv import load_dotenv
from datetime import datetime


load_dotenv()
API_KEY = os.environ.get("HIKERAPI_KEY")
RAPIDAPI_KEY = os.environ.get("FLASHAPI_KEY")
BASE_URL = "https://api.hikerapi.com"

HIKER_HEADERS = {"x-access-key": API_KEY, "accept": "application/json"}
RAPIDAPI_HEADERS = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": "flashapi1.p.rapidapi.com",
    "Content-Type": "application/json"
}

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
URL_RE = re.compile(r'https?://[^\s]+|(?<![\w@])www\.[^\s]+')
MENTION_RE = re.compile(r'(?<!\w)@([A-Za-z0-9_.]+)')
PHONE_RE = re.compile(r'(\+?\d{1,3}[-.\s]?)?\(?\d{3,5}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}')
HASHTAG_RE = re.compile(r'#([A-Za-z0-9_]+)')


# ---------- HikerAPI: profile / graph ----------
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
    r = requests.get(f"{BASE_URL}/gql/user/about",
                      params={"id": user_id}, headers=HIKER_HEADERS)
    r.raise_for_status()
    return r.json()


# ---------- HikerAPI: comments ----------
def get_media_comments(media_pk, max_pages=2):
    comments, max_id, page = [], None, 0
    while page < max_pages:
        params = {"id": media_pk}
        if max_id:
            params["max_id"] = max_id
        r = requests.get(f"{BASE_URL}/v1/media/comments/chunk",
                          params=params, headers=HIKER_HEADERS)
        r.raise_for_status()
        data = r.json()

        if isinstance(data, list) and len(data) >= 2:
            batch, max_id = data[0], data[1]
        else:
            batch, max_id = data, None

        comments.extend(batch)
        page += 1
        if not max_id or not batch:
            break

    return comments


def analyze_commenters(medias, posts_to_sample=10, max_pages_per_post=2, top_n=5):
    """
    Fetches comments from up to `posts_to_sample` posts, dedupes posts and
    comments, and returns only the top `top_n` commenters with their
    unique comment text.
    """
    # dedupe medias by code first — duplicate post entries cause double counting
    seen_codes = set()
    unique_medias = []
    for m in medias:
        code = m.get("code")
        if code and code not in seen_codes:
            seen_codes.add(code)
            unique_medias.append(m)

    sampled = [m for m in unique_medias if m.get("comment_count", 0) > 0][:posts_to_sample]

    overall_counter = Counter()
    post_coverage = defaultdict(set)
    user_comments = defaultdict(list)
    seen_comment_keys = set()

    for m in sampled:
        media_pk = m.get("pk")
        code = m.get("code", "unknown")
        if not media_pk:
            continue

        try:
            comments = get_media_comments(media_pk, max_pages=max_pages_per_post)
        except requests.exceptions.HTTPError:
            continue

        for c in comments:
            user = c.get("user", {}) or {}
            uname = user.get("username")
            text = c.get("text", "")
            created_at = c.get("created_at_utc", "")
            comment_pk = c.get("pk", "")

            if not uname:
                continue

            dedup_key = comment_pk or f"{uname}|{text}|{created_at}|{code}"
            if dedup_key in seen_comment_keys:
                continue
            seen_comment_keys.add(dedup_key)

            overall_counter[uname] += 1
            post_coverage[uname].add(code)
            user_comments[uname].append({
                "post_code": code,
                "text": text,
                "created_at": created_at
            })

    top_commenters = {
        uname: {
            "comment_count": count,
            "posts_commented_on": sorted(post_coverage[uname]),
            "comments": user_comments[uname],
        }
        for uname, count in overall_counter.most_common(top_n)
    }

    return {
        "posts_sampled": len(sampled),
        "top_commenters": top_commenters,
        "note": f"Top {top_n} commenters based on {len(sampled)} posts sampled "
                f"(max {posts_to_sample}), {max_pages_per_post} comment pages each — "
                f"sampled, not exhaustive. Deduplicated by comment ID."
    }

# ---------- Bio signal extraction ----------
def find_external_urls(profile):
    found = []
    if profile.get("external_url"):
        found.append(profile["external_url"])
    for link in profile.get("bio_links", []) or []:
        if isinstance(link, dict) and link.get("url"):
            found.append(link["url"])
        elif isinstance(link, str):
            found.append(link)
    if profile.get("external_lynx_url"):
        found.append(profile["external_lynx_url"])
    return list(dict.fromkeys(found))


def extract_bio_signals(profile):
    bio = profile.get("biography", "") or ""
    external_links = find_external_urls(profile)

    emails_seen = {}
    for e in EMAIL_RE.findall(bio):
        key = e.lower()
        if key not in emails_seen:
            emails_seen[key] = e
    for key_field in ("public_email", "business_email", "email"):
        val = profile.get(key_field)
        if val:
            key = val.lower()
            if key not in emails_seen:
                emails_seen[key] = val

    phones = [p for p in PHONE_RE.findall(bio) if len(re.sub(r'\D', '', p)) >= 7]
    urls_in_bio = URL_RE.findall(bio)
    mentions_in_bio = list(set(MENTION_RE.findall(bio)))
    all_links = list(dict.fromkeys(external_links + urls_in_bio))

    return {
        "emails": list(emails_seen.values()),
        "phones": phones,
        "external_links": all_links,
        "bio_mentions": mentions_in_bio,
    }


def analyze_caption_signals(captions):
    all_mentions, all_hashtags = [], []
    for cap in captions:
        all_mentions.extend(MENTION_RE.findall(cap))
        all_hashtags.extend(HASHTAG_RE.findall(cap))
    return {
        "mention_frequency": dict(Counter(all_mentions).most_common(20)),
        "hashtag_frequency": dict(Counter(all_hashtags).most_common(20)),
    }

def analyze_username_pattern(username):
    reasons, score = [], 0
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
    reasons, score = [], 0
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


def analyze_caption_quality(captions):
    score, reasons = 0, []
    if not captions:
        return score, reasons

    avg_len = sum(len(c) for c in captions) / len(captions)
    if avg_len < 5:
        score += 1
        reasons.append("Captions are extremely short on average")

    all_caps = sum(1 for c in captions if c.upper() == c and len(c) > 5)
    if all_caps / len(captions) > 0.5:
        score += 1
        reasons.append(f"{int(all_caps/len(captions)*100)}% of captions are all-caps")

    import unicodedata
    def is_emoji(char):
        return unicodedata.category(char) in ('So', 'Sm')

    emoji_heavy = 0
    for cap in captions:
        total_chars = len(cap.replace(" ", ""))
        if total_chars == 0:
            continue
        emoji_chars = sum(1 for c in cap if is_emoji(c))
        if emoji_chars / total_chars > 0.5:
            emoji_heavy += 1
    if len(captions) > 0 and emoji_heavy / len(captions) > 0.5:
        score += 0.5
        reasons.append("Most captions are emoji-only (no real text content)")

    spam_keywords = ["follow", "followback", "follow4follow", "f4f",
                      "like4like", "l4l", "dm for promo", "link in bio",
                      "giveaway", "win free"]
    spam_hits = sum(1 for cap in captions if any(kw in cap.lower() for kw in spam_keywords))
    if spam_hits > 0:
        score += spam_hits * 0.5
        reasons.append(f"{spam_hits} captions contain spam/promotional keywords")

    return score, reasons


def analyze_posting_hours(medias):
    score, reasons = 0, []
    hours = []
    for m in medias:
        taken_at = m.get("taken_at", "")
        if taken_at:
            try:
                dt = datetime.fromisoformat(taken_at.replace("Z", "+00:00"))
                hours.append(dt.hour)
            except Exception:
                pass

    if len(hours) < 3:
        return score, reasons

    unique_hours = set(hours)
    if len(unique_hours) == 1:
        score += 1.5
        reasons.append(f"All posts published at exactly hour {hours[0]}:00 — robotic scheduling")
    elif len(unique_hours) <= 2:
        score += 1
        reasons.append("Posts clustered in very narrow time window")

    suspicious_hours = sum(1 for h in hours if 2 <= h <= 5)
    if suspicious_hours / len(hours) > 0.7:
        score += 1
        reasons.append("Majority of posts published between 2-5am (bot-typical hours)")

    return score, reasons


def get_sample_confidence(sampled, total):
    if total == 0:
        return 0
    ratio = sampled / total
    if ratio >= 0.5:
        return 1.0
    elif ratio >= 0.2:
        return 0.7
    elif ratio >= 0.05:
        return 0.4
    else:
        return 0.2


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

    if medias and followers_count > 1000:
        avg_likes = sum(m.get("like_count", 0) for m in medias) / len(medias)
        eng_rate = avg_likes / followers_count
        if eng_rate < 0.001:
            total_score += 2
            all_reasons.append(f"Extremely low engagement ({eng_rate*100:.3f}%)")
        elif eng_rate < 0.005:
            total_score += 1
            all_reasons.append(f"Low engagement rate ({eng_rate*100:.2f}%)")

    u_score, u_reasons = analyze_username_pattern(username)
    total_score += u_score
    all_reasons.extend(u_reasons)

    if not captions and medias:
        captions = [m.get("caption_text", "") for m in medias if m.get("caption_text")]

    c_score, c_reasons = analyze_captions(captions)
    total_score += c_score
    all_reasons.extend(c_reasons)

    cq_score, cq_reasons = analyze_caption_quality(captions)
    total_score += cq_score
    all_reasons.extend(cq_reasons)

    ph_score, ph_reasons = analyze_posting_hours(medias)
    total_score += ph_score
    all_reasons.extend(ph_reasons)

    if followers or following:
        if followers:
            bot_like = sum(1 for uname in followers
                            if isinstance(uname, str) and sum(c.isdigit() for c in uname) >= 4)
            ratio = bot_like / len(followers)
            if ratio > 0.4:
                total_score += 2
                all_reasons.append(f"{int(ratio*100)}% of sampled followers have bot-like usernames")
            elif ratio > 0.2:
                total_score += 1
                all_reasons.append(f"{int(ratio*100)}% of sampled followers have suspicious usernames")

        if following:
            bot_following = sum(1 for uname in following
                                 if isinstance(uname, str) and sum(c.isdigit() for c in uname) >= 4)
            ratio_f = bot_following / len(following)
            if ratio_f > 0.4:
                total_score += 1.5
                all_reasons.append(f"{int(ratio_f*100)}% of following accounts have bot-like usernames")

        if followers and following:
            f_set, fg_set = set(followers), set(following)
            overlap = f_set & fg_set
            overlap_ratio = len(overlap) / max(len(f_set), 1)
            if len(f_set) >= 10:
                if overlap_ratio > 0.7:
                    total_score += 2
                    all_reasons.append(
                        f"{int(overlap_ratio*100)}% overlap between followers and following "
                        f"({len(overlap)} accounts) — possible mutual follow farm")
                elif overlap_ratio > 0.4:
                    total_score += 1
                    all_reasons.append(f"{int(overlap_ratio*100)}% overlap between followers and following")

        if followers_count > 0:
            confidence = get_sample_confidence(len(followers), followers_count)
            if confidence < 0.5:
                all_reasons.append(
                    f"Note: follower analysis based on {len(followers)} sampled "
                    f"out of {followers_count} total — low confidence")

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
def investigate(username, posts_to_sample_for_comments=10, comment_pages_per_post=2):
    hiker_profile = get_profile(username)
    user_id = hiker_profile["pk"]
    is_private = hiker_profile.get("is_private", False)

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
        "bio_signals": {},
        "captions": [],
        "caption_signals": {},
        "media_urls": [],
        "followers": [],
        "following": [],
        "similar_accounts": [],
        "commenter_analysis": {},
        "fake_account_analysis": None,
    }

    result["bio_signals"] = extract_bio_signals(hiker_profile)

    try:
        about = get_user_about(user_id)
        result["account_details"] = {
            "country": about.get("country", ""),
            "joined_date": about.get("date", ""),
            "former_usernames_count": about.get("former_usernames", ""),
            "is_verified": about.get("is_verified", False),
        }
    except requests.exceptions.HTTPError as e:
        print(f"[!] Could not fetch additional info: {e}", file=sys.stderr)

    medias = []

    if is_private:
        try:
            similar_raw = get_mutual_accounts(user_id)
            users = similar_raw.get("users", []) if isinstance(similar_raw, dict) else similar_raw
            result["similar_accounts"] = list(dict.fromkeys(
                item.get("username", "") for item in users
                if isinstance(item, dict) and item.get("username")
            ))
        except requests.exceptions.HTTPError as e:
            print(f"[!] Could not fetch mutual accounts: {e}", file=sys.stderr)
    else:
        try:
            medias = get_medias(user_id)

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
                    "comment_count": m.get("comment_count", 0),
                    "caption": m.get("caption_text", ""),
                }
                for m in medias if m.get("code")
            ]

            seen_captions = set()
            for m in medias:
                cap = m.get("caption_text", "").strip()
                if cap and cap not in seen_captions:
                    seen_captions.add(cap)
                    result["captions"].append(cap)

            result["caption_signals"] = analyze_caption_signals(result["captions"])

        except requests.exceptions.HTTPError as e:
            print(f"[!] Could not fetch posts: {e}", file=sys.stderr)

        try:
            raw_followers = get_followers(user_id)
            result["followers"] = list(dict.fromkeys(
                u.get("username") for u in raw_followers if u.get("username")
            ))
        except requests.exceptions.HTTPError as e:
            print(f"[!] Could not fetch followers: {e}", file=sys.stderr)

        try:
            raw_following = get_following(user_id)
            result["following"] = list(dict.fromkeys(
                u.get("username") for u in raw_following if u.get("username")
            ))
        except requests.exceptions.HTTPError as e:
            print(f"[!] Could not fetch following: {e}", file=sys.stderr)

        if medias:
            try:
                result["commenter_analysis"] = analyze_commenters(
                    medias,
                    posts_to_sample=10,
                    max_pages_per_post=2,
                    top_n=5
                )
            except Exception as e:
                print(f"[!] Could not analyze commenters: {e}", file=sys.stderr)
    result["fake_account_analysis"] = fake_account_score(
        result, medias, result["followers"], result["following"]
    )

    return result

def save_result(result, folder="data"):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{result['username']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    return path

# ---------- Entry point ----------
if __name__ == "__main__":
    if not API_KEY:
        print("[-] HIKERAPI_KEY not found in environment / .env file", file=sys.stderr)
        raise SystemExit(1)

    username = input("Enter Instagram username: ").strip()
    if not username:
        print("[-] No username entered.", file=sys.stderr)
        raise SystemExit(1)

    try:
        data = investigate(username)
        save_result(data)
    except requests.exceptions.HTTPError as e:
        print(f"[-] API error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[-] Something went wrong: {e}", file=sys.stderr)