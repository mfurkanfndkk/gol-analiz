"""
Web Scraper — Mackolik (birincil), SofaScore (Selenium), FlashScore
Bot koruması bypass: cloudscraper, random UA/delay, Selenium undetected.
Exponential backoff + JSON disk cache.
"""

import re
import os
import json
import time
import random
import hashlib
import traceback
import unicodedata
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse
from pathlib import Path

import requests
import cloudscraper
from bs4 import BeautifulSoup

from analysis_engine import TeamStats, H2HRecord


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
]

# Disk cache dizini
CACHE_DIR = Path(__file__).parent / '.cache'
CACHE_DIR.mkdir(exist_ok=True)
CACHE_TTL = 3600 * 4  # 4 saat cache ömrü


def _cache_key(url: str) -> str:
    """URL'den cache key üret."""
    return hashlib.md5(url.encode()).hexdigest()


def _get_cached_html(url: str) -> Optional[str]:
    """Cache'den HTML getir (TTL geçerli ise)."""
    key = _cache_key(url)
    cache_file = CACHE_DIR / f'{key}.html'
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < CACHE_TTL:
            print(f'[Cache] HIT: {url[:60]}... ({age:.0f}s old)')
            return cache_file.read_text(encoding='utf-8')
        else:
            print(f'[Cache] EXPIRED: {url[:60]}... ({age:.0f}s)')
    return None


def _save_cache_html(url: str, html: str):
    """HTML'i disk cache'e yaz."""
    key = _cache_key(url)
    cache_file = CACHE_DIR / f'{key}.html'
    cache_file.write_text(html, encoding='utf-8')


def _get_session() -> cloudscraper.CloudScraper:
    browser_choices = [
        {'browser': 'chrome', 'platform': 'windows', 'desktop': True},
        {'browser': 'firefox', 'platform': 'windows', 'desktop': True},
    ]
    scraper = cloudscraper.create_scraper(
        browser=random.choice(browser_choices)
    )
    scraper.headers.update({
        'User-Agent': random.choice(USER_AGENTS),
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Referer': 'https://arsiv.mackolik.com/',
    })
    return scraper


def _fetch_with_backoff(url: str, max_retries: int = 4, base_delay: float = 5.0) -> Optional[str]:
    """
    Exponential backoff ile URL fetch et.
    Önce cache'e bak, yoksa iste.
    429'da: base_delay * 2^retry şeklinde bekle.
    """
    # Cache kontrolü
    cached = _get_cached_html(url)
    if cached:
        return cached

    for attempt in range(max_retries):
        session = _get_session()
        delay = base_delay * (2 ** attempt) + random.uniform(0, 2)

        if attempt > 0:
            print(f'[Fetch] Retry {attempt}/{max_retries}, {delay:.0f}s bekleniyor... {url[:60]}')
            time.sleep(delay)
        else:
            _random_delay(2.0, 4.0)

        try:
            resp = session.get(url, timeout=20)
            if resp.status_code == 200:
                _save_cache_html(url, resp.text)
                return resp.text
            elif resp.status_code == 429:
                print(f'[Fetch] 429 Rate limit (attempt {attempt+1}/{max_retries})')
                continue
            else:
                print(f'[Fetch] HTTP {resp.status_code}: {url[:60]}')
                if resp.status_code < 500:
                    return None
                continue
        except Exception as e:
            print(f'[Fetch] Error: {e}')
            continue

    print(f'[Fetch] FAILED after {max_retries} retries: {url[:60]}')
    return None


def _random_delay(min_s=0.5, max_s=1.5):
    time.sleep(random.uniform(min_s, max_s))


def _normalize_turkish(text: str) -> str:
    """Türkçe karakterleri ASCII'ye dönüştür, lowercase. Encoding sorunlarını aşar."""
    # İlk: Python str olarak normalize et
    text = text.lower()
    # Türkçe karakter map
    tr_map = str.maketrans({
        'ı': 'i', 'İ': 'i', 'ğ': 'g', 'Ğ': 'g',
        'ü': 'u', 'Ü': 'u', 'ş': 's', 'Ş': 's',
        'ö': 'o', 'Ö': 'o', 'ç': 'c', 'Ç': 'c',
        '\u0131': 'i',  # ı
        '\u00fc': 'u',  # ü
        '\u015f': 's',  # ş
        '\u00f6': 'o',  # ö
        '\u00e7': 'c',  # ç
        '\u011f': 'g',  # ğ
    })
    text = text.translate(tr_map)
    # NFD decompose ile kalan aksanları sil
    text = unicodedata.normalize('NFD', text)
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    return text


# ============================================================
# MACKOLIK SCRAPER (birincil — en iyi çalışan)
# ============================================================

class MackolikScraper:
    """Mackolik.com — puan durumu, maç detayları, istatistikler."""

    def __init__(self):
        self.session = _get_session()

    def parse_match_id(self, url: str) -> Optional[str]:
        match = re.search(r'/Mac/(\d+)', url)
        return match.group(1) if match else None

    def parse_teams_from_url(self, url: str) -> Tuple[str, str]:
        """URL'den takım isimlerini çıkar."""
        match = re.search(r'/Mac/\d+/([^/?#]+)', url)
        if match:
            teams_str = match.group(1)
            parts = teams_str.split('-')
            if len(parts) == 2:
                return parts[0], parts[1]
            elif len(parts) > 2:
                mid = len(parts) // 2
                return '-'.join(parts[:mid]), '-'.join(parts[mid:])
        return "Ev Sahibi", "Deplasman"

    def scrape(self, url: str) -> Dict:
        """Mackolik maç sayfasından tüm verileri çek."""
        # Her scrape'de yeni session (cookie rate limit bypass)
        self.session = _get_session()
        result = {
            'source': 'mackolik',
            'success': False,
            'error': None,
            'home_team': None,
            'away_team': None,
            'h2h': None,
            'league_avg': 2.5,
            'match_info': {}
        }

        try:
            html = _fetch_with_backoff(url, max_retries=3, base_delay=5.0)
            if not html:
                result['error'] = 'Fetch failed after retries'
                return result

            soup = BeautifulSoup(html, 'lxml')
            home_name, away_name = self.parse_teams_from_url(url)

            # Başlıktan takım isimlerini al
            title = soup.find('title')
            if title:
                title_text = title.get_text(strip=True)
                result['match_info']['title'] = title_text
                tm = re.match(r'(.+?)\s*-\s*(.+?)\s*\(', title_text)
                if tm:
                    home_name = tm.group(1).strip()
                    away_name = tm.group(2).strip()

            result['match_info']['home_team'] = home_name
            result['match_info']['away_team'] = away_name

            # Puan durumu tablosundan temel istatistikleri çek
            standings = self._parse_standings(soup)

            # Takım sayfalarından detaylı son maç verisi çek
            home_team_link = self._find_team_link(soup, home_name)
            away_team_link = self._find_team_link(soup, away_name)

            print(f"[Mackolik] Ev sahibi link: {home_team_link}")
            print(f"[Mackolik] Deplasman link: {away_team_link}")

            home_stats = TeamStats(name=home_name)
            away_stats = TeamStats(name=away_name)

            # Takım sayfasından zenginleştir
            if home_team_link:
                self._enrich_from_team_page(home_stats, home_team_link)
            if away_team_link:
                self._enrich_from_team_page(away_stats, away_team_link)

            # Takım sayfasından veri gelemediyse standings'den doldur
            if standings and home_stats.matches_played == 0:
                st = self._find_team_in_standings(standings, home_name)
                if st:
                    home_stats = st

            if standings and away_stats.matches_played == 0:
                st = self._find_team_in_standings(standings, away_name)
                if st:
                    away_stats = st

            result['home_team'] = home_stats
            result['away_team'] = away_stats

            # Lig ortalaması
            if standings:
                result['league_avg'] = self._calc_league_avg(standings)

            # H2H — takım sayfalarından eski sezonları tarayarak
            home_base_url = self._extract_team_base_url(home_team_link)
            away_base_url = self._extract_team_base_url(away_team_link)
            if home_base_url or away_base_url:
                h2h = self._fetch_h2h_from_seasons(
                    home_base_url or away_base_url,
                    home_name, away_name
                )
                if h2h:
                    result['h2h'] = h2h

            result['success'] = True

        except Exception as e:
            result['error'] = str(e)
            traceback.print_exc()

        return result

    def _find_team_link(self, soup, team_name: str) -> Optional[str]:
        """Puan durumu tablosundan takım sayfası linkini bul. Sezonlu link tercih et."""
        links = soup.find_all('a', href=True)
        team_norm = _normalize_turkish(team_name)
        best_link = None

        for a in links:
            href = a.get('href', '')
            if '/Takim/' not in href:
                continue
            text = a.get_text(strip=True)
            text_norm = _normalize_turkish(text)

            if team_norm in text_norm or text_norm in team_norm or self._fuzzy_match(team_norm, text_norm):
                full_url = href
                if full_url.startswith('//'):
                    full_url = 'https:' + full_url
                elif full_url.startswith('/'):
                    full_url = 'https://arsiv.mackolik.com' + full_url
                # Sezonlu link tercih et (2025/2026, 2026/2027 gibi)
                if re.search(r'/\d{4}/\d{4}', full_url):
                    return full_url
                if not best_link:
                    best_link = full_url

        return best_link

    def _enrich_from_team_page(self, stats: TeamStats, team_url: str):
        """Takım sayfasından son maçları çekip istatistikleri doldur."""
        try:
            html = _fetch_with_backoff(team_url, max_retries=4, base_delay=8.0)
            if not html:
                print(f"[Mackolik] Team page fetch failed: {team_url}")
                return

            soup = BeautifulSoup(html, 'lxml')
            tables = soup.find_all('table')

            print(f"[Mackolik] {stats.name} — {len(tables)} tablo bulundu")

            # Maç tablolarını bul — "IY" ve ("2.5" veya "KG") sütunları olanlar
            match_tables = []
            for idx, t in enumerate(tables):
                rows = t.find_all('tr')
                if len(rows) < 3:
                    continue
                header_raw = rows[0].get_text(strip=True)
                header_norm = _normalize_turkish(header_raw)

                # Maç tablosu: IY sütunu olmalı
                has_match_cols = ('iy' in header_norm and ('2.5' in header_raw or 'kg' in header_norm))
                if not has_match_cols:
                    continue

                # Yerel lig kontrolü: normalize edilmiş header'da ara
                is_domestic = any(kw in header_norm for kw in [
                    'turkiye', 'super lig', 'trendyol', 'spor toto', 'sueper lig',
                    'sueperlig', 'superlig'
                ])

                # Satır sayısı (skorlu olanlar)
                score_count = 0
                for row in rows[1:]:
                    cells = row.find_all('td')
                    for c in cells:
                        if re.match(r'^\d+\s*-\s*\d+$', c.get_text(strip=True)):
                            score_count += 1
                            break

                match_tables.append({
                    'table': t,
                    'index': idx,
                    'is_domestic': is_domestic,
                    'header_raw': header_raw[:80],
                    'header_norm': header_norm[:80],
                    'rows': len(rows),
                    'scores': score_count
                })
                print(f"[Mackolik]   Tablo {idx}: domestic={is_domestic}, rows={len(rows)}, scores={score_count}, header='{header_norm[:60]}'")

            if not match_tables:
                print(f"[Mackolik] {stats.name} — Maç tablosu bulunamadı")
                return

            # Strateji: domestic olanı seç, yoksa İLK maç tablosunu al
            # Mackolik takım sayfasında ilk maç tablosu her zaman yerel ligdir
            selected = None
            for mt in match_tables:
                if mt['is_domestic']:
                    selected = mt
                    break
            if not selected:
                # İlk maç tablosu = yerel lig (Mackolik convention)
                selected = match_tables[0]

            print(f"[Mackolik] {stats.name} — Tablo {selected['index']} seçildi (domestic={selected['is_domestic']}, scores={selected['scores']})")

            matches_table = selected['table']
            rows = matches_table.find_all('tr')[1:]  # Header'ı atla

            total_gf = 0
            total_ga = 0
            match_count = 0

            for row in rows:
                cells = row.find_all('td')
                texts = [c.get_text(strip=True) for c in cells]

                # Skor pattern: "X - Y"
                score_text = None
                score_idx = -1
                for i, t in enumerate(texts):
                    if re.match(r'^\d+\s*-\s*\d+$', t):
                        score_text = t
                        score_idx = i
                        break

                if not score_text:
                    continue

                score_match = re.match(r'(\d+)\s*-\s*(\d+)', score_text)
                if not score_match:
                    continue

                h_score = int(score_match.group(1))
                a_score = int(score_match.group(2))

                # Ev sahibi / deplasman takım isimlerini bul
                # Pattern: ['tarih', '', 'EV_TAKIM', '', 'X - Y', '', 'DEP_TAKIM', ...]
                home_team_text = ''
                away_team_text = ''
                for i in range(score_idx - 1, -1, -1):
                    if texts[i] and len(texts[i]) > 2 and not re.match(r'^\d', texts[i]):
                        home_team_text = texts[i]
                        break
                for i in range(score_idx + 1, len(texts)):
                    if texts[i] and len(texts[i]) > 2 and not re.match(r'^\d', texts[i]) and texts[i] not in ['Ü', 'A', 'KG', '-', 'G', 'B', 'M']:
                        away_team_text = texts[i]
                        break

                team_norm = _normalize_turkish(stats.name)
                home_norm = _normalize_turkish(home_team_text) if home_team_text else ''
                away_norm = _normalize_turkish(away_team_text) if away_team_text else ''

                is_home = self._fuzzy_match(team_norm, home_norm) if home_norm else False
                is_away = self._fuzzy_match(team_norm, away_norm) if away_norm else False

                if not is_home and not is_away:
                    # Tüm hücrelerde ara
                    for i in range(score_idx):
                        if texts[i] and self._fuzzy_match(team_norm, _normalize_turkish(texts[i])):
                            is_home = True
                            break
                    if not is_home:
                        for i in range(score_idx + 1, len(texts)):
                            if texts[i] and self._fuzzy_match(team_norm, _normalize_turkish(texts[i])):
                                is_away = True
                                break

                if is_home:
                    gf, ga = h_score, a_score
                elif is_away:
                    gf, ga = a_score, h_score
                else:
                    continue

                total_gf += gf
                total_ga += ga
                match_count += 1

                # Form
                if gf > ga:
                    stats.form_results.append('W')
                elif gf == ga:
                    stats.form_results.append('D')
                else:
                    stats.form_results.append('L')

                # Ev/Deplasman
                if is_home:
                    stats.home_played += 1
                    stats.home_gf += gf
                    stats.home_ga += ga
                    if gf > ga: stats.home_wins += 1
                    elif gf == ga: stats.home_draws += 1
                    else: stats.home_losses += 1
                else:
                    stats.away_played += 1
                    stats.away_gf += gf
                    stats.away_ga += ga
                    if gf > ga: stats.away_wins += 1
                    elif gf == ga: stats.away_draws += 1
                    else: stats.away_losses += 1

                stats.recent_matches.append({
                    'goals_for': gf,
                    'goals_against': ga,
                    'opponent': away_team_text if is_home else home_team_text,
                    'venue': 'home' if is_home else 'away',
                })

                # === HT skor, Ü/A, KG çıkarma ===
                # Mackolik row: [..., 'HT_SKOR', '', 'Ü/A', 'KG', ...]
                # HT skor genellikle score_idx+4 civarında (score sonrası 2. skor pattern'i)
                ht_score = None
                for i in range(score_idx + 1, len(texts)):
                    ht_m = re.match(r'^(\d+)\s*-\s*(\d+)$', texts[i])
                    if ht_m and i != score_idx:
                        ht_score = (int(ht_m.group(1)), int(ht_m.group(2)))
                        break

                if ht_score:
                    ht_h, ht_a = ht_score
                    if is_home:
                        stats.ht_goals_for += ht_h
                        stats.ht_goals_against += ht_a
                    else:
                        stats.ht_goals_for += ht_a
                        stats.ht_goals_against += ht_h
                    stats.ht_matches += 1
                    stats.recent_matches[-1]['ht_gf'] = ht_h if is_home else ht_a
                    stats.recent_matches[-1]['ht_ga'] = ht_a if is_home else ht_h

                # Ü/A ve KG: textte 'Ü' veya 'A' ve 'KG' veya '-' ara
                for t in texts[score_idx + 1:]:
                    if t == 'Ü' or t == '\u00dc':  # Ü
                        stats.over25_count += 1
                        break
                    elif t == 'A':
                        break  # Alt, saymıyoruz

                for t in texts[score_idx + 1:]:
                    if t == 'KG':
                        stats.btts_count += 1
                        break

            # Toplamları güncelle
            if match_count > 0:
                stats.matches_played = match_count
                stats.goals_for = total_gf
                stats.goals_against = total_ga
                stats.wins = stats.form_results.count('W')
                stats.draws = stats.form_results.count('D')
                stats.losses = stats.form_results.count('L')
                stats.form_gf = total_gf
                stats.form_ga = total_ga
                stats.form_matches = match_count

            print(f"[Mackolik] {stats.name}: {match_count} mac, {total_gf}AG-{total_ga}YG, "
                  f"Ev:{stats.home_played}({stats.home_gf}G-{stats.home_ga}Y) "
                  f"Dep:{stats.away_played}({stats.away_gf}G-{stats.away_ga}Y) "
                  f"HT:{stats.ht_matches}mac({stats.ht_goals_for}G-{stats.ht_goals_against}Y) "
                  f"KG:{stats.btts_count} U25:{stats.over25_count} "
                  f"Form:{''.join(stats.form_results)}")

        except Exception as e:
            print(f"[Mackolik] Team page error: {e}")
            traceback.print_exc()

    def _parse_standings(self, soup) -> Optional[List[Dict]]:
        """Puan durumu tablosunu parse et."""
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            if len(rows) < 5:
                continue

            header_row = rows[0]
            header_cells = header_row.find_all(['th', 'td'])
            header_texts = [c.get_text(strip=True) for c in header_cells]

            # "O G B M P" pattern'i ara
            if 'O' in header_texts and ('G' in header_texts or 'P' in header_texts):
                standings = []
                for row in rows[1:]:
                    cells = row.find_all('td')
                    if len(cells) < 6:
                        continue

                    texts = [c.get_text(strip=True) for c in cells]
                    try:
                        team_name = ''
                        for t in texts:
                            if t and not t.isdigit() and len(t) > 2:
                                team_name = t
                                break

                        nums = [int(t) for t in texts if t.isdigit()]
                        if len(nums) >= 5:
                            idx = 0
                            if nums[0] <= 20:  # sıra numarası
                                idx = 1
                            played = nums[idx] if idx < len(nums) else 0
                            wins = nums[idx + 1] if idx + 1 < len(nums) else 0
                            draws = nums[idx + 2] if idx + 2 < len(nums) else 0
                            losses = nums[idx + 3] if idx + 3 < len(nums) else 0
                            points = nums[-1] if nums else 0

                            gf = ga = 0
                            if len(nums) > 6:
                                gf = nums[idx + 4] if idx + 4 < len(nums) else 0
                                ga = nums[idx + 5] if idx + 5 < len(nums) else 0

                        if team_name and played > 0:
                            standings.append({
                                'name': team_name,
                                'played': played,
                                'wins': wins,
                                'draws': draws,
                                'losses': losses,
                                'gf': gf,
                                'ga': ga,
                                'points': points
                            })
                    except (ValueError, IndexError):
                        continue

                if standings:
                    return standings
        return None

    def _find_team_in_standings(self, standings: List[Dict], team_name: str) -> Optional[TeamStats]:
        """Puan durumundan takım istatistiklerini bul."""
        team_norm = _normalize_turkish(team_name)

        for team in standings:
            name_norm = _normalize_turkish(team['name'])
            if (team_norm in name_norm or name_norm in team_norm or
                    self._fuzzy_match(team_norm, name_norm)):

                stats = TeamStats(
                    name=team['name'],
                    matches_played=team['played'],
                    wins=team['wins'],
                    draws=team['draws'],
                    losses=team['losses'],
                    goals_for=team.get('gf', 0),
                    goals_against=team.get('ga', 0),
                )

                # Gol verileri yoksa tahmini hesapla
                if stats.goals_for == 0 and stats.matches_played > 0:
                    stats.goals_for = round(stats.wins * 2.0 + stats.draws * 1.0 + stats.losses * 0.6)
                    stats.goals_against = round(stats.wins * 0.5 + stats.draws * 1.0 + stats.losses * 2.0)

                # Ev/Deplasman tahmini dağılım
                hp = stats.matches_played // 2
                ap = stats.matches_played - hp
                stats.home_played = hp
                stats.away_played = ap

                if stats.matches_played > 0:
                    home_win_ratio = 0.55
                    stats.home_wins = round(stats.wins * home_win_ratio)
                    stats.away_wins = stats.wins - stats.home_wins
                    stats.home_draws = stats.draws // 2
                    stats.away_draws = stats.draws - stats.home_draws
                    stats.home_losses = hp - stats.home_wins - stats.home_draws
                    stats.away_losses = ap - stats.away_wins - stats.away_draws

                    if stats.goals_for > 0:
                        stats.home_gf = round(stats.goals_for * 0.55)
                        stats.away_gf = stats.goals_for - stats.home_gf
                        stats.home_ga = round(stats.goals_against * 0.45)
                        stats.away_ga = stats.goals_against - stats.home_ga

                return stats
        return None

    def _fuzzy_match(self, name1: str, name2: str) -> bool:
        """Fuzzy matching — normalize edilmiş isimler için."""
        if not name1 or not name2:
            return False
        # Direkt eşleşme
        if name1 == name2:
            return True
        # İçerme
        if name1 in name2 or name2 in name1:
            return True
        # Kelime eşleşmesi
        words1 = set(name1.split())
        words2 = set(name2.split())
        common = words1 & words2
        if common and any(len(w) > 2 for w in common):
            return True
        # İlk 5 karakter (sadece alfanümerik)
        clean1 = re.sub(r'[^a-z0-9]', '', name1)
        clean2 = re.sub(r'[^a-z0-9]', '', name2)
        if len(clean1) > 4 and len(clean2) > 4:
            if clean1[:6] == clean2[:6]:
                return True
        return False

    def _calc_league_avg(self, standings: List[Dict]) -> float:
        """Lig gol ortalamasını hesapla."""
        total_goals = 0
        total_matches = 0
        has_goals = False

        for team in standings:
            if team.get('gf', 0) > 0:
                has_goals = True
                total_goals += team['gf']
                total_matches += team['played']

        if has_goals and total_matches > 0:
            return total_goals / total_matches
        else:
            total_wins = sum(t['wins'] for t in standings)
            total_draws = sum(t['draws'] for t in standings)
            total_played = sum(t['played'] for t in standings)
            if total_played > 0:
                matches = total_played / 2
                est_goals = total_wins * 2.8 + total_draws * 2.0
                return est_goals / matches if matches > 0 else 2.5
            return 2.5

    def _extract_team_base_url(self, team_url: Optional[str]) -> Optional[str]:
        """Takım URL'inden base URL çıkar: /Takim/ID/Name"""
        if not team_url:
            return None
        m = re.search(r'(https?://arsiv\.mackolik\.com/Takim/\d+/[^/]+)', team_url)
        return m.group(1) if m else None

    def _fetch_h2h_from_seasons(self, team_base_url: str, home_name: str, away_name: str) -> Optional[H2HRecord]:
        """Son 3 sezonun takım sayfalarını tarayıp iki takım arasındaki maçları bul."""
        from datetime import datetime
        current_year = datetime.now().year
        seasons = []
        for y in range(current_year, current_year - 3, -1):
            seasons.append(f"{y}/{y+1}")
            seasons.append(f"{y-1}/{y}")

        # Unique sezonlar
        seen = set()
        unique_seasons = []
        for s in seasons:
            if s not in seen:
                seen.add(s)
                unique_seasons.append(s)

        home_norm = _normalize_turkish(home_name)
        away_norm = _normalize_turkish(away_name)
        # Karşı takımı belirle (URL'deki takım değil, karşı taraf)
        url_team_norm = _normalize_turkish(team_base_url.split('/')[-1])
        if self._fuzzy_match(url_team_norm, home_norm):
            target_norm = away_norm
            target_name = away_name
            search_is_home = True  # URL'deki takım ev sahibi
        else:
            target_norm = home_norm
            target_name = home_name
            search_is_home = False

        h2h = H2HRecord()

        for season in unique_seasons[:4]:
            season_url = f"{team_base_url}/{season}"
            try:
                html = _fetch_with_backoff(season_url, max_retries=3, base_delay=10.0)
                if not html:
                    continue

                soup = BeautifulSoup(html, 'lxml')
                tables = soup.find_all('table')

                for t in tables:
                    rows = t.find_all('tr')
                    if len(rows) < 3:
                        continue
                    header_norm = _normalize_turkish(rows[0].get_text(strip=True))
                    if 'iy' not in header_norm:
                        continue

                    for row in rows[1:]:
                        cells = row.find_all('td')
                        texts = [c.get_text(strip=True) for c in cells]
                        row_norm = _normalize_turkish(' '.join(texts))

                        if target_norm[:5] not in row_norm:
                            continue

                        # Skor bul
                        score_text = None
                        score_idx = -1
                        for i, txt in enumerate(texts):
                            if re.match(r'^\d+\s*-\s*\d+$', txt):
                                score_text = txt
                                score_idx = i
                                break

                        if not score_text:
                            continue

                        sm = re.match(r'(\d+)\s*-\s*(\d+)', score_text)
                        if not sm:
                            continue

                        h_score = int(sm.group(1))
                        a_score = int(sm.group(2))

                        # Kim ev sahibi?
                        row_home_text = ''
                        for i in range(score_idx - 1, -1, -1):
                            if texts[i] and len(texts[i]) > 2 and not re.match(r'^\d', texts[i]):
                                row_home_text = texts[i]
                                break

                        row_home_norm = _normalize_turkish(row_home_text)
                        # home_name maçın ev sahibi mi?
                        home_is_row_home = self._fuzzy_match(_normalize_turkish(home_name), row_home_norm)

                        if home_is_row_home:
                            hg, ag = h_score, a_score
                        else:
                            hg, ag = a_score, h_score

                        h2h.total_matches += 1
                        h2h.home_goals += hg
                        h2h.away_goals += ag

                        if hg > ag:
                            h2h.home_wins += 1
                        elif hg == ag:
                            h2h.draws += 1
                        else:
                            h2h.away_wins += 1

                        # Tarih bul
                        date_str = ''
                        for txt in texts:
                            if re.match(r'^\d{1,2}\.\d{1,2}\.\d{4}$', txt):
                                date_str = txt
                                break

                        h2h.results.append({
                            'home_score': hg,
                            'away_score': ag,
                            'home_team': home_name,
                            'away_team': away_name,
                            'date': date_str
                        })

            except Exception as e:
                print(f"[Mackolik H2H] Season {season} error: {e}")
                continue

        if h2h.total_matches > 0:
            print(f"[Mackolik H2H] {h2h.total_matches} maç: {home_name} {h2h.home_wins}G-{h2h.draws}B-{h2h.away_wins}M {away_name}, "
                  f"Gol: {h2h.home_goals}-{h2h.away_goals}")
            return h2h

        return None

    def _fetch_h2h(self, match_id: str, home_name: str, away_name: str) -> Optional[H2HRecord]:
        """H2H verisi çek — Mackolik'in karsilastirma endpoint'i genelde standings döndürüyor.
        Alternatif: ana sayfadaki maç istatistiklerinden çıkar."""
        try:
            # Karşılaştırma sayfası
            h2h_url = f'https://arsiv.mackolik.com/Mac/{match_id}/Karsilastirma'
            _random_delay(0.5, 1.0)
            resp = self.session.get(h2h_url, timeout=15)
            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, 'lxml')
            tables = soup.find_all('table')

            home_norm = _normalize_turkish(home_name)
            away_norm = _normalize_turkish(away_name)
            h2h = H2HRecord()

            for table in tables:
                rows = table.find_all('tr')
                header = rows[0].get_text(strip=True) if rows else ''
                header_norm = _normalize_turkish(header)

                # H2H tablosu: her iki takım adını da içermeli veya "karşılaşma" kelimesi
                is_h2h_table = (
                    (home_norm[:5] in header_norm and away_norm[:5] in header_norm) or
                    'karsilasma' in header_norm or
                    'son mac' in header_norm
                )

                if not is_h2h_table:
                    continue

                for row in rows[1:]:
                    cells = row.find_all('td')
                    if len(cells) < 3:
                        continue
                    texts = [c.get_text(strip=True) for c in cells]

                    score_match = None
                    for t in texts:
                        score_match = re.search(r'(\d+)\s*[-:]\s*(\d+)', t)
                        if score_match:
                            break

                    if score_match:
                        h_score = int(score_match.group(1))
                        a_score = int(score_match.group(2))
                        h2h.total_matches += 1
                        h2h.home_goals += h_score
                        h2h.away_goals += a_score

                        if h_score > a_score:
                            h2h.home_wins += 1
                        elif h_score == a_score:
                            h2h.draws += 1
                        else:
                            h2h.away_wins += 1

                        h2h.results.append({
                            'home_score': h_score,
                            'away_score': a_score,
                            'home_team': home_name,
                            'away_team': away_name,
                            'date': 0
                        })

            if h2h.total_matches > 0:
                print(f"[Mackolik H2H] {h2h.total_matches} maç bulundu: {home_name} {h2h.home_wins}G-{h2h.draws}B-{h2h.away_wins}M {away_name}")
                return h2h

            return None

        except Exception as e:
            print(f"[Mackolik H2H] Error: {e}")
            return None


# ============================================================
# FLASHSCORE SCRAPER
# ============================================================

class FlashScoreScraper:
    """FlashScore — HTML parse. JS-heavy site, veri sınırlı olabilir."""

    def __init__(self):
        self.session = _get_session()

    def scrape(self, url: str) -> Dict:
        result = {'source': 'flashscore', 'success': False, 'error': None}
        try:
            _random_delay(1.0, 2.0)
            resp = self.session.get(url, timeout=20)
            if resp.status_code != 200:
                result['error'] = f'HTTP {resp.status_code}'
                return result

            soup = BeautifulSoup(resp.text, 'lxml')
            title = soup.find('title')
            if title:
                result['page_title'] = title.get_text(strip=True)

            result['html_length'] = len(resp.text)
            result['success'] = True
        except Exception as e:
            result['error'] = str(e)
        return result


# ============================================================
# SOFASCORE SCRAPER (Selenium tabanlı)
# ============================================================

class SofaScoreScraper:
    """SofaScore — Selenium ile headless browser. Cloudflare + bot korumasını aşar."""

    def __init__(self):
        self.driver = None

    def _init_driver(self):
        if self.driver:
            return

        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager

            options = Options()
            options.add_argument('--headless=new')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument('--disable-infobars')
            options.add_argument(f'--user-agent={random.choice(USER_AGENTS)}')
            options.add_experimental_option('excludeSwitches', ['enable-automation'])
            options.add_experimental_option('useAutomationExtension', False)
            options.add_argument('--window-size=1920,1080')

            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)

            self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': '''
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    window.chrome = { runtime: {} };
                '''
            })
        except Exception as e:
            print(f"[SofaScore] Selenium init hatası: {e}")
            self.driver = None

    def parse_url(self, url: str) -> Optional[int]:
        match = re.search(r'#id[:\s]*(\d+)', url)
        if match:
            return int(match.group(1))
        return None

    def scrape(self, url: str) -> Dict:
        result = {
            'source': 'sofascore',
            'success': False,
            'error': None,
            'home_team': None,
            'away_team': None,
            'h2h': None,
            'league_avg': 2.5,
            'match_info': {}
        }

        self._init_driver()
        if not self.driver:
            result['error'] = 'Chrome WebDriver başlatılamadı'
            return result

        try:
            event_id = self.parse_url(url)
            if not event_id:
                result['error'] = 'Event ID bulunamadı'
                return result

            page_url = url.split('#')[0] if '#' in url else url
            self.driver.get(page_url)
            time.sleep(5)

            api_script = f"""
            return fetch('/api/v1/event/{event_id}')
                .then(r => r.json())
                .catch(e => ({{error: e.message}}));
            """
            event_data = self.driver.execute_script(api_script)
            if event_data and 'event' in event_data:
                ev = event_data['event']
                home_info = ev.get('homeTeam', {})
                away_info = ev.get('awayTeam', {})
                tournament = ev.get('tournament', {}).get('uniqueTournament', {})
                season = ev.get('season', {})

                result['match_info'] = {
                    'home_team': home_info.get('name', ''),
                    'away_team': away_info.get('name', ''),
                    'tournament': tournament.get('name', ''),
                }

                home_id = home_info.get('id')
                away_id = away_info.get('id')
                tournament_id = tournament.get('id')
                season_id = season.get('id')

                if home_id and tournament_id and season_id:
                    home_stats_data = self.driver.execute_script(f"""
                    return fetch('/api/v1/team/{home_id}/unique-tournament/{tournament_id}/season/{season_id}/statistics/overall')
                        .then(r => r.json()).catch(e => null);
                    """)
                    if home_stats_data:
                        result['home_team'] = self._parse_team_stats(home_stats_data, home_info.get('name', ''))

                    time.sleep(1)
                    away_stats_data = self.driver.execute_script(f"""
                    return fetch('/api/v1/team/{away_id}/unique-tournament/{tournament_id}/season/{season_id}/statistics/overall')
                        .then(r => r.json()).catch(e => null);
                    """)
                    if away_stats_data:
                        result['away_team'] = self._parse_team_stats(away_stats_data, away_info.get('name', ''))

                if home_id:
                    time.sleep(1)
                    recent_home = self.driver.execute_script(f"""
                    return fetch('/api/v1/team/{home_id}/events/last/0')
                        .then(r => r.json()).catch(e => null);
                    """)
                    if recent_home and result.get('home_team'):
                        self._fill_recent(recent_home, result['home_team'], home_id)

                if away_id:
                    time.sleep(1)
                    recent_away = self.driver.execute_script(f"""
                    return fetch('/api/v1/team/{away_id}/events/last/0')
                        .then(r => r.json()).catch(e => null);
                    """)
                    if recent_away and result.get('away_team'):
                        self._fill_recent(recent_away, result['away_team'], away_id)

                # H2H
                time.sleep(1)
                h2h_data = self.driver.execute_script(f"""
                return fetch('/api/v1/event/{event_id}/h2h/events')
                    .then(r => r.json()).catch(e => null);
                """)
                if h2h_data:
                    result['h2h'] = self._parse_h2h(h2h_data, home_id, away_id)

                # Puan durumu
                if tournament_id and season_id:
                    time.sleep(1)
                    standings_data = self.driver.execute_script(f"""
                    return fetch('/api/v1/unique-tournament/{tournament_id}/season/{season_id}/standings/total')
                        .then(r => r.json()).catch(e => null);
                    """)
                    if standings_data:
                        result['league_avg'] = self._calc_league_avg(standings_data)

                result['success'] = True
            else:
                result['error'] = 'Event verisi alınamadı'

        except Exception as e:
            result['error'] = str(e)
            traceback.print_exc()

        return result

    def _parse_team_stats(self, data: Dict, name: str) -> TeamStats:
        stats = TeamStats(name=name)
        s = data.get('statistics', {})
        if s:
            stats.matches_played = s.get('matches', 0)
            stats.wins = s.get('wins', 0)
            stats.draws = s.get('draws', 0)
            stats.losses = s.get('losses', 0)
            stats.goals_for = s.get('goalsScored', 0)
            stats.goals_against = s.get('goalsConceded', 0)
        return stats

    def _fill_recent(self, data: Dict, stats: TeamStats, team_id: int):
        events = data.get('events', [])
        for ev in events[-10:]:
            hs = ev.get('homeScore', {}).get('current', 0)
            aws = ev.get('awayScore', {}).get('current', 0)
            is_home = ev.get('homeTeam', {}).get('id') == team_id

            gf = hs if is_home else aws
            ga = aws if is_home else hs

            if gf > ga:
                stats.form_results.append('W')
            elif gf == ga:
                stats.form_results.append('D')
            else:
                stats.form_results.append('L')

            stats.form_gf += gf
            stats.form_ga += ga
            stats.form_matches += 1

            if is_home:
                stats.home_played += 1
                stats.home_gf += gf
                stats.home_ga += ga
                if gf > ga: stats.home_wins += 1
                elif gf == ga: stats.home_draws += 1
                else: stats.home_losses += 1
            else:
                stats.away_played += 1
                stats.away_gf += gf
                stats.away_ga += ga
                if gf > ga: stats.away_wins += 1
                elif gf == ga: stats.away_draws += 1
                else: stats.away_losses += 1

            stats.recent_matches.append({
                'goals_for': gf,
                'goals_against': ga,
                'opponent': ev.get('awayTeam' if is_home else 'homeTeam', {}).get('name', ''),
                'venue': 'home' if is_home else 'away',
            })

    def _parse_h2h(self, data: Dict, home_id: int, away_id: int) -> Optional[H2HRecord]:
        events = data.get('events', [])
        if not events:
            return None

        h2h = H2HRecord()
        for ev in events:
            hs = ev.get('homeScore', {}).get('current', 0)
            aws = ev.get('awayScore', {}).get('current', 0)
            ev_home_id = ev.get('homeTeam', {}).get('id')

            if ev_home_id == home_id:
                hg, ag = hs, aws
            else:
                hg, ag = aws, hs

            h2h.total_matches += 1
            h2h.home_goals += hg
            h2h.away_goals += ag
            if hg > ag: h2h.home_wins += 1
            elif hg == ag: h2h.draws += 1
            else: h2h.away_wins += 1

            h2h.results.append({
                'home_score': hg, 'away_score': ag,
                'home_team': ev.get('homeTeam', {}).get('name', ''),
                'away_team': ev.get('awayTeam', {}).get('name', ''),
                'date': ev.get('startTimestamp', 0)
            })

        return h2h if h2h.total_matches > 0 else None

    def _calc_league_avg(self, data: Dict) -> float:
        try:
            rows = data.get('standings', [{}])[0].get('rows', [])
            tg = sum(r.get('scoresFor', 0) + r.get('scoresAgainst', 0) for r in rows)
            tm = sum(r.get('matches', 0) for r in rows)
            return tg / tm if tm > 0 else 2.5
        except:
            return 2.5

    def close(self):
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
            self.driver = None


# ============================================================
# ANA SCRAPER ORKESTRATÖRÜ
# ============================================================

class MatchScraper:
    """Tüm kaynakları orkestre eden ana scraper."""

    def __init__(self):
        self.mackolik = MackolikScraper()
        self.flashscore = FlashScoreScraper()
        self.sofascore = SofaScoreScraper()

    def identify_source(self, url: str) -> str:
        if 'sofascore' in url:
            return 'sofascore'
        elif 'flashscore' in url:
            return 'flashscore'
        elif 'mackolik' in url:
            return 'mackolik'
        return 'unknown'

    def scrape_urls(self, urls: List[str]) -> Dict:
        """
        Birden fazla URL'den veri çek, birleştir.
        Öncelik: SofaScore > Mackolik > FlashScore
        """
        all_data = {
            'sources': {},
            'home_team': None,
            'away_team': None,
            'h2h': None,
            'league_avg': 2.5,
            'match_info': {},
            'errors': []
        }

        for url in urls:
            url = url.strip()
            if not url:
                continue

            source = self.identify_source(url)
            print(f"[Scraper] {source} kaynağından çekiliyor: {url[:80]}...")

            try:
                if source == 'sofascore':
                    data = self.sofascore.scrape(url)
                    all_data['sources']['sofascore'] = {'success': data.get('success')}
                    if data.get('success'):
                        if data.get('home_team'):
                            all_data['home_team'] = data['home_team']
                        if data.get('away_team'):
                            all_data['away_team'] = data['away_team']
                        if data.get('h2h'):
                            all_data['h2h'] = data['h2h']
                        all_data['league_avg'] = data.get('league_avg', 2.5)
                        all_data['match_info'] = data.get('match_info', {})
                    self.sofascore.close()

                elif source == 'mackolik':
                    data = self.mackolik.scrape(url)
                    all_data['sources']['mackolik'] = {'success': data.get('success')}
                    if data.get('success'):
                        if not all_data['home_team'] and data.get('home_team'):
                            all_data['home_team'] = data['home_team']
                        if not all_data['away_team'] and data.get('away_team'):
                            all_data['away_team'] = data['away_team']
                        if not all_data['h2h'] and data.get('h2h'):
                            all_data['h2h'] = data['h2h']
                        if data.get('league_avg', 2.5) != 2.5:
                            all_data['league_avg'] = data['league_avg']
                        if not all_data['match_info'] and data.get('match_info'):
                            all_data['match_info'] = data['match_info']

                elif source == 'flashscore':
                    data = self.flashscore.scrape(url)
                    all_data['sources']['flashscore'] = {'success': data.get('success')}

                else:
                    all_data['errors'].append(f'Bilinmeyen kaynak: {url}')

            except Exception as e:
                all_data['errors'].append(f'{source}: {str(e)}')
                traceback.print_exc()

        # Fallback
        if not all_data['home_team']:
            name = all_data.get('match_info', {}).get('home_team', 'Ev Sahibi')
            all_data['home_team'] = TeamStats(name=name)
        if not all_data['away_team']:
            name = all_data.get('match_info', {}).get('away_team', 'Deplasman')
            all_data['away_team'] = TeamStats(name=name)

        return all_data


def scrape_and_prepare(urls: List[str]) -> tuple:
    """URL listesinden veri çek, analiz motoruna uygun formata dönüştür."""
    scraper = MatchScraper()
    raw = scraper.scrape_urls(urls)

    home = raw.get('home_team') or TeamStats(name="Ev Sahibi")
    away = raw.get('away_team') or TeamStats(name="Deplasman")
    h2h = raw.get('h2h')
    league_avg = raw.get('league_avg', 2.5)

    return home, away, h2h, league_avg, raw
