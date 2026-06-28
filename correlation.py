"""
EchoMark - Account Correlation Engine
Compares two Instagram accounts across multiple signals.
Run after insta.py has collected data for both accounts.
"""

import json
import os
from datetime import datetime

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from Levenshtein import distance as levenshtein_distance


def load_profile(username):
    cache_path = f"data/{username}.json"
    if not os.path.exists(cache_path):
        raise FileNotFoundError(
            f"No cached data found for '{username}'. "
            f"Run insta.py on this account first."
        )
    with open(cache_path, encoding="utf-8") as f:
        return json.load(f)


# ---------- Signal functions ----------

def signal_username_similarity(u1, u2):
    dist = levenshtein_distance(u1, u2)
    max_len = max(len(u1), len(u2), 1)
    score = round((1 - dist / max_len) * 100)
    detail = f"Levenshtein distance: {dist}"
    return score, detail


def signal_follower_overlap(data1, data2):
    f1 = set(data1.get("followers", []))
    f2 = set(data2.get("followers", []))
    if not f1 or not f2:
        return 0, "Insufficient follower data", []
    shared = f1 & f2
    score = round(len(shared) / max(len(f1), len(f2)) * 100)
    detail = f"{len(shared)} shared accounts out of {max(len(f1), len(f2))} sampled"
    return score, detail, sorted(shared)[:10]


def signal_following_overlap(data1, data2):
    fg1 = set(data1.get("following", []))
    fg2 = set(data2.get("following", []))
    if not fg1 or not fg2:
        return 0, "Insufficient following data", []
    shared = fg1 & fg2
    score = round(len(shared) / max(len(fg1), len(fg2)) * 100)
    detail = f"{len(shared)} shared accounts out of {max(len(fg1), len(fg2))} sampled"
    return score, detail, sorted(shared)[:10]


def signal_caption_similarity(data1, data2):
    c1 = data1.get("captions", [])
    c2 = data2.get("captions", [])
    if not c1 or not c2:
        return 0, "Insufficient caption data"
    try:
        corpus = [" ".join(c1), " ".join(c2)]
        vec = TfidfVectorizer()
        matrix = vec.fit_transform(corpus)
        score = round(
            cosine_similarity(matrix[0:1], matrix[1:2])[0][0] * 100
        )
        return score, f"Based on {len(c1)} vs {len(c2)} captions"
    except Exception:
        return 0, "Could not compute caption similarity"


def signal_bio_similarity(data1, data2):
    b1 = data1.get("bio", "")
    b2 = data2.get("bio", "")
    if not b1 or not b2:
        return 0, "One or both accounts have empty bio"
    try:
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3))
        matrix = vec.fit_transform([b1, b2])
        score = round(
            cosine_similarity(matrix[0:1], matrix[1:2])[0][0] * 100
        )
        b1_preview = b1[:40] + ("..." if len(b1) > 40 else "")
        b2_preview = b2[:40] + ("..." if len(b2) > 40 else "")
        return score, f"'{b1_preview}' vs '{b2_preview}'"
    except Exception:
        return 0, "Could not compute bio similarity"


def signal_posting_hour_overlap(data1, data2):
    def get_hours(data):
        hours = []
        for m in data.get("media_urls", []):
            ts = m.get("taken_at_ts", 0)
            if ts:
                try:
                    hours.append(datetime.fromtimestamp(ts).hour)
                except Exception:
                    pass
        return hours

    h1 = get_hours(data1)
    h2 = get_hours(data2)

    if not h1 or not h2:
        return 0, "No post timestamp data available"

    def peak_window(hours):
        avg = sum(hours) / len(hours)
        return int(avg - 1), int(avg + 1)

    def hour_to_ampm(h):
        suffix = "am" if h < 12 else "pm"
        h12 = h % 12 or 12
        return f"{h12}{suffix}"

    w1s, w1e = peak_window(h1)
    w2s, w2e = peak_window(h2)
    overlap = max(0, min(w1e, w2e) - max(w1s, w2s))
    score = min(round((overlap / 3) * 100), 100)

    if overlap > 0:
        detail = (
            f"Both post between "
            f"{hour_to_ampm(max(w1s, w2s))}-{hour_to_ampm(min(w1e, w2e))}"
        )
    else:
        detail = (
            f"Account 1 peaks {hour_to_ampm(w1s)}-{hour_to_ampm(w1e)}, "
            f"Account 2 peaks {hour_to_ampm(w2s)}-{hour_to_ampm(w2e)}"
        )

    return score, detail


def signal_country_match(data1, data2):
    c1 = data1.get("account_details", {}).get("country", "")
    c2 = data2.get("account_details", {}).get("country", "")
    if not c1 or not c2:
        return 0, "Country data unavailable for one or both accounts"
    if c1.lower() == c2.lower():
        return 100, f"Yes ({c1})"
    return 0, f"No ({c1} vs {c2})"


def signal_joined_proximity(data1, data2):
    j1 = data1.get("account_details", {}).get("joined_date", "")
    j2 = data2.get("account_details", {}).get("joined_date", "")
    if not j1 or not j2:
        return 0, "Joined date unavailable for one or both accounts"
    try:
        d1 = datetime.strptime(j1, "%B %Y")
        d2 = datetime.strptime(j2, "%B %Y")
        diff = abs((d1.year - d2.year) * 12 + (d1.month - d2.month))
        if diff == 0:
            return 100, f"Same month ({j1})"
        elif diff <= 3:
            return 75, f"{diff} month{'s' if diff > 1 else ''} apart"
        elif diff <= 6:
            return 50, f"{diff} months apart"
        elif diff <= 12:
            return 25, f"{diff} months apart"
        else:
            years = diff // 12
            months = diff % 12
            parts = []
            if years:
                parts.append(f"{years} year{'s' if years > 1 else ''}")
            if months:
                parts.append(f"{months} month{'s' if months > 1 else ''}")
            return 0, f"{' and '.join(parts)} apart ({j1} vs {j2})"
    except ValueError:
        return 0, f"Could not parse dates ({j1} vs {j2})"


# ---------- Main correlation function ----------

def correlate_accounts(data1, data2):
    u1 = data1.get("username", "")
    u2 = data2.get("username", "")

    u_score,  u_detail               = signal_username_similarity(u1, u2)
    f_score,  f_detail,  f_shared    = signal_follower_overlap(data1, data2)
    fg_score, fg_detail, fg_shared   = signal_following_overlap(data1, data2)
    c_score,  c_detail               = signal_caption_similarity(data1, data2)
    b_score,  b_detail               = signal_bio_similarity(data1, data2)
    ph_score, ph_detail              = signal_posting_hour_overlap(data1, data2)
    co_score, co_detail              = signal_country_match(data1, data2)
    j_score,  j_detail               = signal_joined_proximity(data1, data2)

    # weighted scoring — followers/captions weighted higher
    weights = [
        (u_score,  1),   # username similarity
        (f_score,  2),   # follower overlap
        (fg_score, 2),   # following overlap
        (c_score,  2),   # caption similarity
        (b_score,  1),   # bio similarity
        (ph_score, 1),   # posting hour overlap
        (co_score, 1),   # same country
        (j_score,  1),   # joined proximity
    ]

    weighted_total = sum(score * w for score, w in weights)
    max_possible = sum(100 * w for _, w in weights)
    overall = round(weighted_total / max_possible * 100)

    if overall >= 70:
        verdict = "HIGHLY LIKELY LINKED"
    elif overall >= 45:
        verdict = "POSSIBLY LINKED"
    elif overall >= 20:
        verdict = "WEAK CORRELATION"
    else:
        verdict = "LIKELY UNRELATED"

    return {
        "account_1": u1,
        "account_2": u2,
        "signals": {
            "username_similarity":  {"score": u_score,  "detail": u_detail},
            "follower_overlap":     {"score": f_score,  "detail": f_detail,  "shared": f_shared},
            "following_overlap":    {"score": fg_score, "detail": fg_detail, "shared": fg_shared},
            "caption_similarity":   {"score": c_score,  "detail": c_detail},
            "bio_similarity":       {"score": b_score,  "detail": b_detail},
            "posting_hour_overlap": {"score": ph_score, "detail": ph_detail},
            "same_country":         {"score": co_score, "detail": co_detail},
            "joined_proximity":     {"score": j_score,  "detail": j_detail},
        },
        "overall_correlation": overall,
        "verdict": verdict,
    }


# ---------- Display ----------

def print_correlation(corr):
    u1 = corr["account_1"]
    u2 = corr["account_2"]

    print(f"\n===== CORRELATION ANALYSIS =====")
    print(f"  Account 1     : {u1}")
    print(f"  Account 2     : {u2}")
    print()

    rows = [
        ("Username similarity",  "username_similarity"),
        ("Follower overlap",     "follower_overlap"),
        ("Following overlap",    "following_overlap"),
        ("Caption similarity",   "caption_similarity"),
        ("Bio similarity",       "bio_similarity"),
        ("Posting hour overlap", "posting_hour_overlap"),
        ("Same country",         "same_country"),
        ("Joined proximity",     "joined_proximity"),
    ]

    for label, key in rows:
        sig = corr["signals"].get(key, {})
        score = sig.get("score", 0)
        detail = sig.get("detail", "")
        shared = sig.get("shared", [])
        print(f"  {label:<25}: {score}%")
        print(f"    {detail}")
        if shared:
            print(f"    Shared: {', '.join(shared[:5])}")

    print()
    print(f"  {'Overall correlation':<25}: {corr['overall_correlation']}%")
    print(f"  Verdict        : {corr['verdict']}")


# ---------- Entry point ----------

if __name__ == "__main__":
    username1 = input("Enter first Instagram username: ").strip()
    username2 = input("Enter second Instagram username: ").strip()

    try:
        data1 = load_profile(username1)
        data2 = load_profile(username2)

        corr = correlate_accounts(data1, data2)
        print_correlation(corr)

        path = f"data/correlation_{username1}_vs_{username2}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(corr, f, indent=2, ensure_ascii=False)
        print(f"\n[+] Saved to: {path}")

    except FileNotFoundError as e:
        print(f"[-] {e}")
    except Exception as e:
        print(f"[-] Something went wrong: {e}")