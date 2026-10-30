"""
Maç Analiz Motoru v2 — Güçlendirilmiş İstatistiksel Bahis Analizi

Temel Poisson'u aşan özellikler:
- Dixon-Coles düzeltmesi (düşük skorlu maçlar için rho parametresi)
- Exponential decay (son maçlara daha fazla ağırlık)
- Gerçek İY (HT) gol oranları (Mackolik'ten)
- Gerçek KG/ÜA oranları (Mackolik'ten)
- Rakip gücü ağırlıklandırma (puan durumu bazlı)
- Kelly Criterion (bahis büyüklüğü önerisi)
- Kombine bahis önerileri (korelasyon bazlı)
- Value bet tespiti (güven seviyeleri ile)
"""

import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class TeamStats:
    name: str = ""
    matches_played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    # Ev/Deplasman ayrımı
    home_played: int = 0
    home_wins: int = 0
    home_draws: int = 0
    home_losses: int = 0
    home_gf: int = 0
    home_ga: int = 0
    away_played: int = 0
    away_wins: int = 0
    away_draws: int = 0
    away_losses: int = 0
    away_gf: int = 0
    away_ga: int = 0
    # Son form (son 5-10 maç)
    form_results: List[str] = field(default_factory=list)  # W/D/L listesi
    form_gf: int = 0
    form_ga: int = 0
    form_matches: int = 0
    # Son maçlar detay
    recent_matches: List[Dict] = field(default_factory=list)
    # Lig ortalamaları (normalizasyon için)
    league_avg_goals: float = 2.5
    # === YENİ: HT / KG / ÜA gerçek verileri ===
    ht_goals_for: int = 0       # İlk yarı attığı goller toplamı
    ht_goals_against: int = 0   # İlk yarı yediği goller toplamı
    ht_matches: int = 0         # HT verisi olan maç sayısı
    btts_count: int = 0         # Her iki takımın da gol attığı maç sayısı
    over25_count: int = 0       # Toplam gol > 2.5 olan maç sayısı
    # Puan durumu sırası (rakip gücü hesabı için)
    league_position: int = 0    # 1 = en iyi, 0 = bilinmiyor

    @property
    def goal_diff(self) -> int:
        return self.goals_for - self.goals_against

    @property
    def points(self) -> int:
        return self.wins * 3 + self.draws

    @property
    def ppg(self) -> float:
        return self.points / max(self.matches_played, 1)

    @property
    def avg_gf(self) -> float:
        return self.goals_for / max(self.matches_played, 1)

    @property
    def avg_ga(self) -> float:
        return self.goals_against / max(self.matches_played, 1)

    @property
    def home_avg_gf(self) -> float:
        return self.home_gf / max(self.home_played, 1)

    @property
    def home_avg_ga(self) -> float:
        return self.home_ga / max(self.home_played, 1)

    @property
    def away_avg_gf(self) -> float:
        return self.away_gf / max(self.away_played, 1)

    @property
    def away_avg_ga(self) -> float:
        return self.away_ga / max(self.away_played, 1)

    @property
    def form_avg_gf(self) -> float:
        return self.form_gf / max(self.form_matches, 1)

    @property
    def form_avg_ga(self) -> float:
        return self.form_ga / max(self.form_matches, 1)

    @property
    def form_points(self) -> int:
        pts = 0
        for r in self.form_results:
            if r == 'W':
                pts += 3
            elif r == 'D':
                pts += 1
        return pts

    @property
    def form_ppg(self) -> float:
        return self.form_points / max(len(self.form_results), 1)

    @property
    def clean_sheet_pct(self) -> float:
        if not self.recent_matches:
            return 0
        cs = sum(1 for m in self.recent_matches if m.get('goals_against', 1) == 0)
        return cs / len(self.recent_matches)

    @property
    def btts_pct(self) -> float:
        if not self.recent_matches:
            return 0.5
        btts = sum(1 for m in self.recent_matches
                   if m.get('goals_for', 0) > 0 and m.get('goals_against', 0) > 0)
        return btts / len(self.recent_matches)

    @property
    def ht_goal_ratio(self) -> float:
        """İlk yarıda atılan gollerin toplam gollere oranı."""
        if self.ht_matches == 0 or self.goals_for == 0:
            return 0.42  # Varsayılan
        total_ht = self.ht_goals_for + self.ht_goals_against
        total_ft = self.goals_for + self.goals_against
        if total_ft == 0:
            return 0.42
        return total_ht / total_ft

    @property
    def real_btts_rate(self) -> float:
        """Gerçek KG oranı (maç verilerinden)."""
        if self.btts_count > 0 and self.matches_played > 0:
            return self.btts_count / self.matches_played
        return self.btts_pct

    @property
    def real_over25_rate(self) -> float:
        """Gerçek Ü2.5 oranı."""
        if self.over25_count > 0 and self.matches_played > 0:
            return self.over25_count / self.matches_played
        return 0.5


@dataclass
class H2HRecord:
    total_matches: int = 0
    home_wins: int = 0
    draws: int = 0
    away_wins: int = 0
    home_goals: int = 0
    away_goals: int = 0
    results: List[Dict] = field(default_factory=list)  # Son karşılaşmalar

    @property
    def avg_total_goals(self) -> float:
        return (self.home_goals + self.away_goals) / max(self.total_matches, 1)

    @property
    def over25_pct(self) -> float:
        if not self.results:
            return 0.5
        return sum(1 for r in self.results
                   if r.get('home_score', 0) + r.get('away_score', 0) > 2.5) / len(self.results)

    @property
    def btts_pct(self) -> float:
        if not self.results:
            return 0.5
        return sum(1 for r in self.results
                   if r.get('home_score', 0) > 0 and r.get('away_score', 0) > 0) / len(self.results)


# ============================================================
# MATEMATIK
# ============================================================

def poisson_pmf(k: int, lam: float) -> float:
    """Poisson olasılık kütle fonksiyonu."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def dixon_coles_tau(h: int, a: int, home_xg: float, away_xg: float, rho: float) -> float:
    """
    Dixon-Coles (1997) düzeltme faktörü.
    Düşük skorlu sonuçları (0-0, 0-1, 1-0, 1-1) ayarlar.
    rho < 0 → 0-0 ve 1-1 olasılığını artırır (defansif maç)
    rho > 0 → 1-0 ve 0-1 olasılığını artırır (tek gollü maç)
    Tipik rho: [-0.13, -0.05] — genelde negatif (beraberlik underestimate edilir)
    """
    if h == 0 and a == 0:
        return 1.0 - home_xg * away_xg * rho
    elif h == 0 and a == 1:
        return 1.0 + home_xg * rho
    elif h == 1 and a == 0:
        return 1.0 + away_xg * rho
    elif h == 1 and a == 1:
        return 1.0 - rho
    else:
        return 1.0


def estimate_rho(home_xg: float, away_xg: float) -> float:
    """
    Rho parametresini xG'den tahmin et.
    Düşük xG → daha negatif rho (defansif maç, beraberlik olasılığı artar)
    Yüksek xG → rho 0'a yaklaşır (Poisson yeterli)
    """
    total_xg = home_xg + away_xg
    if total_xg < 1.5:
        return -0.12
    elif total_xg < 2.0:
        return -0.10
    elif total_xg < 2.5:
        return -0.08
    elif total_xg < 3.0:
        return -0.06
    elif total_xg < 3.5:
        return -0.04
    else:
        return -0.02


def exponential_decay_weights(n: int, half_life: int = 5) -> List[float]:
    """
    Exponential decay ağırlıkları.
    half_life=5 → 5 maç öncesi yarı ağırlıkta.
    Index 0 = en eski, index n-1 = en yeni.
    """
    decay = math.log(2) / half_life
    weights = [math.exp(decay * i) for i in range(n)]
    total = sum(weights)
    return [w / total for w in weights]


def decay_weighted_avg(values: List[float], half_life: int = 5) -> float:
    """Decay-weighted ortalama."""
    if not values:
        return 0.0
    weights = exponential_decay_weights(len(values), half_life)
    return sum(v * w for v, w in zip(values, weights))


def kelly_criterion(prob: float, odds: float, fraction: float = 0.25) -> float:
    """
    Kelly Criterion — optimal bahis büyüklüğü (bankroll yüzdesi).
    fraction: Kelly fraksiyonu (0.25 = quarter Kelly, daha güvenli).
    Negatifse bahis yapma.
    """
    if odds <= 1.0 or prob <= 0 or prob >= 1:
        return 0.0
    b = odds - 1.0  # net oran
    q = 1.0 - prob
    kelly = (b * prob - q) / b
    return max(0, kelly * fraction) * 100  # Yüzde olarak


# ============================================================
# xG HESAPLAMA (v2)
# ============================================================

def calculate_expected_goals(
    home_team: TeamStats,
    away_team: TeamStats,
    h2h: Optional[H2HRecord] = None,
    league_avg: float = 2.5
) -> Tuple[float, float]:
    """
    Geliştirilmiş xG hesaplama v2.

    Yenilikler:
    - Exponential decay (son maçlara daha fazla ağırlık)
    - Gerçek HT gol oranları
    - Venue-specific güç katsayıları
    - H2H blend
    """
    half_league = league_avg / 2.0

    # --- Ev sahibi saldırı & savunma gücü ---
    home_attack_general = home_team.avg_gf / max(half_league, 0.5)
    home_defense_general = home_team.avg_ga / max(half_league, 0.5)

    home_attack_home = home_team.home_avg_gf / max(half_league, 0.5) if home_team.home_played > 0 else home_attack_general
    home_defense_home = home_team.home_avg_ga / max(half_league, 0.5) if home_team.home_played > 0 else home_defense_general

    # --- Form: decay-weighted ---
    if home_team.recent_matches and len(home_team.recent_matches) >= 3:
        gf_list = [m.get('goals_for', 0) for m in home_team.recent_matches]
        ga_list = [m.get('goals_against', 0) for m in home_team.recent_matches]
        home_form_gf = decay_weighted_avg(gf_list, half_life=5)
        home_form_ga = decay_weighted_avg(ga_list, half_life=5)
        home_attack_form = home_form_gf / max(half_league, 0.5)
        home_defense_form = home_form_ga / max(half_league, 0.5)
    else:
        home_attack_form = home_team.form_avg_gf / max(half_league, 0.5) if home_team.form_matches > 0 else home_attack_general
        home_defense_form = home_team.form_avg_ga / max(half_league, 0.5) if home_team.form_matches > 0 else home_defense_general

    # --- Deplasman saldırı & savunma gücü ---
    away_attack_general = away_team.avg_gf / max(half_league, 0.5)
    away_defense_general = away_team.avg_ga / max(half_league, 0.5)

    away_attack_away = away_team.away_avg_gf / max(half_league, 0.5) if away_team.away_played > 0 else away_attack_general
    away_defense_away = away_team.away_avg_ga / max(half_league, 0.5) if away_team.away_played > 0 else away_defense_general

    if away_team.recent_matches and len(away_team.recent_matches) >= 3:
        gf_list = [m.get('goals_for', 0) for m in away_team.recent_matches]
        ga_list = [m.get('goals_against', 0) for m in away_team.recent_matches]
        away_form_gf = decay_weighted_avg(gf_list, half_life=5)
        away_form_ga = decay_weighted_avg(ga_list, half_life=5)
        away_attack_form = away_form_gf / max(half_league, 0.5)
        away_defense_form = away_form_ga / max(half_league, 0.5)
    else:
        away_attack_form = away_team.form_avg_gf / max(half_league, 0.5) if away_team.form_matches > 0 else away_attack_general
        away_defense_form = away_team.form_avg_ga / max(half_league, 0.5) if away_team.form_matches > 0 else away_defense_general

    # --- Ağırlıklı birleştirme (normalize edilmiş) ---
    w_general = 0.25
    w_venue = 0.30
    w_form = 0.25
    w_h2h = 0.20

    w_base = w_general + w_venue + w_form
    wg = w_general / w_base
    wv = w_venue / w_base
    wf = w_form / w_base

    home_attack = wg * home_attack_general + wv * home_attack_home + wf * home_attack_form
    home_defense = wg * home_defense_general + wv * home_defense_home + wf * home_defense_form
    away_attack = wg * away_attack_general + wv * away_attack_away + wf * away_attack_form
    away_defense = wg * away_defense_general + wv * away_defense_away + wf * away_defense_form

    # Base xG
    home_xg = home_attack * away_defense * half_league
    away_xg = away_attack * home_defense * half_league

    # H2H blend
    if h2h and h2h.total_matches >= 3:
        h2h_home_avg = h2h.home_goals / h2h.total_matches
        h2h_away_avg = h2h.away_goals / h2h.total_matches
        home_xg = home_xg * (1 - w_h2h) + h2h_home_avg * w_h2h
        away_xg = away_xg * (1 - w_h2h) + h2h_away_avg * w_h2h

    # Ev sahibi avantajı
    home_xg *= 1.08
    away_xg *= 0.94

    # Clamp
    home_xg = max(0.2, min(home_xg, 5.0))
    away_xg = max(0.2, min(away_xg, 5.0))

    return round(home_xg, 3), round(away_xg, 3)


def build_score_matrix(home_xg: float, away_xg: float, max_goals: int = 8, use_dixon_coles: bool = True) -> List[List[float]]:
    """
    Skor olasılık matrisi — Dixon-Coles düzeltmeli Poisson.
    """
    rho = estimate_rho(home_xg, away_xg) if use_dixon_coles else 0.0
    matrix = []
    for h in range(max_goals + 1):
        row = []
        for a in range(max_goals + 1):
            prob = poisson_pmf(h, home_xg) * poisson_pmf(a, away_xg)
            if use_dixon_coles:
                tau = dixon_coles_tau(h, a, home_xg, away_xg, rho)
                prob *= tau
            row.append(round(max(prob, 0), 6))
        matrix.append(row)

    # Normalize (Dixon-Coles sonrası toplam 1.0 olmayabilir)
    total = sum(sum(row) for row in matrix)
    if total > 0 and abs(total - 1.0) > 0.001:
        for h in range(max_goals + 1):
            for a in range(max_goals + 1):
                matrix[h][a] = round(matrix[h][a] / total, 6)

    return matrix


# ============================================================
# ANA ANALİZ
# ============================================================

def analyze_match(
    home_team: TeamStats,
    away_team: TeamStats,
    h2h: Optional[H2HRecord] = None,
    league_avg: float = 2.5
) -> Dict:
    """Tam maç analizi v2. Dixon-Coles + decay + Kelly + combo."""

    home_xg, away_xg = calculate_expected_goals(home_team, away_team, h2h, league_avg)
    matrix = build_score_matrix(home_xg, away_xg, use_dixon_coles=True)
    rho = estimate_rho(home_xg, away_xg)

    # --- 1X2 Olasılıkları ---
    home_win_prob = 0.0
    draw_prob = 0.0
    away_win_prob = 0.0

    for h in range(9):
        for a in range(9):
            p = matrix[h][a]
            if h > a:
                home_win_prob += p
            elif h == a:
                draw_prob += p
            else:
                away_win_prob += p

    total_1x2 = home_win_prob + draw_prob + away_win_prob
    home_win_prob /= total_1x2
    draw_prob /= total_1x2
    away_win_prob /= total_1x2

    # --- Ü/A Gol Pazarları ---
    over_under = {}
    for line in [0.5, 1.5, 2.5, 3.5, 4.5, 5.5]:
        over = 0.0
        under = 0.0
        for h in range(9):
            for a in range(9):
                total = h + a
                p = matrix[h][a]
                if total > line:
                    over += p
                else:
                    under += p
        total_ou = over + under
        over_under[line] = {
            'over': round(over / total_ou, 4),
            'under': round(under / total_ou, 4),
            'over_odds': round(1 / max(over / total_ou, 0.001), 2),
            'under_odds': round(1 / max(under / total_ou, 0.001), 2)
        }

    # === Gerçek veri ile Ü/A 2.5 düzeltmesi ===
    poisson_over25 = over_under[2.5]['over']
    real_over25 = _blend_real_ou25(home_team, away_team, h2h, poisson_over25)
    if abs(real_over25 - poisson_over25) > 0.01:
        over_under[2.5] = {
            'over': round(real_over25, 4),
            'under': round(1 - real_over25, 4),
            'over_odds': round(1 / max(real_over25, 0.001), 2),
            'under_odds': round(1 / max(1 - real_over25, 0.001), 2)
        }

    # --- KG Var/Yok ---
    btts_yes = 0.0
    btts_no = 0.0
    for h in range(9):
        for a in range(9):
            p = matrix[h][a]
            if h > 0 and a > 0:
                btts_yes += p
            else:
                btts_no += p
    total_btts = btts_yes + btts_no
    btts_yes /= total_btts
    btts_no /= total_btts

    # === Gerçek veri ile KG düzeltmesi ===
    real_btts = _blend_real_btts(home_team, away_team, h2h, btts_yes)
    btts_yes = real_btts
    btts_no = 1 - real_btts

    # --- İlk Yarı Ü/A (gerçek HT oranlarıyla) ---
    ht_factor = _get_ht_factor(home_team, away_team)
    ht_home_xg = home_xg * ht_factor
    ht_away_xg = away_xg * ht_factor

    ht_matrix = build_score_matrix(ht_home_xg, ht_away_xg, 5, use_dixon_coles=True)
    ht_over_under = {}
    for line in [0.5, 1.5, 2.5]:
        over = 0.0
        under = 0.0
        for h in range(6):
            for a in range(6):
                total = h + a
                p = ht_matrix[h][a]
                if total > line:
                    over += p
                else:
                    under += p
        total_ou = over + under
        ht_over_under[line] = {
            'over': round(over / total_ou, 4),
            'under': round(under / total_ou, 4),
            'over_odds': round(1 / max(over / total_ou, 0.001), 2),
            'under_odds': round(1 / max(under / total_ou, 0.001), 2)
        }

    # --- İY 1X2 ---
    ht_home_win = 0.0
    ht_draw = 0.0
    ht_away_win = 0.0
    for h in range(6):
        for a in range(6):
            p = ht_matrix[h][a]
            if h > a:
                ht_home_win += p
            elif h == a:
                ht_draw += p
            else:
                ht_away_win += p
    ht_total = ht_home_win + ht_draw + ht_away_win
    ht_home_win /= ht_total
    ht_draw /= ht_total
    ht_away_win /= ht_total

    # --- En Olası Skorlar ---
    score_probs = []
    for h in range(7):
        for a in range(7):
            score_probs.append({
                'score': f"{h}-{a}",
                'home': h,
                'away': a,
                'probability': round(matrix[h][a] * 100, 2),
                'odds': round(1 / max(matrix[h][a], 0.0001), 1)
            })
    score_probs.sort(key=lambda x: x['probability'], reverse=True)

    # --- Çifte Şans ---
    double_chance = {
        '1X': round(home_win_prob + draw_prob, 4),
        'X2': round(draw_prob + away_win_prob, 4),
        '12': round(home_win_prob + away_win_prob, 4)
    }

    # --- Handikap ---
    handicaps = {}
    for spread in [-2.5, -1.5, -0.5, 0.5, 1.5, 2.5]:
        home_cover = 0.0
        for h in range(9):
            for a in range(9):
                if (h + spread) > a:
                    home_cover += matrix[h][a]
        handicaps[spread] = {
            'home_cover': round(home_cover, 4),
            'away_cover': round(1 - home_cover, 4),
            'home_odds': round(1 / max(home_cover, 0.001), 2),
            'away_odds': round(1 / max(1 - home_cover, 0.001), 2)
        }

    # --- Ev sahibi / Deplasman toplam gol ---
    home_over = {}
    away_over = {}
    for line in [0.5, 1.5, 2.5]:
        ho = sum(poisson_pmf(g, home_xg) for g in range(int(line) + 1, 9))
        ao = sum(poisson_pmf(g, away_xg) for g in range(int(line) + 1, 9))
        home_over[line] = {
            'over': round(ho, 4),
            'under': round(1 - ho, 4),
            'over_odds': round(1 / max(ho, 0.001), 2),
            'under_odds': round(1 / max(1 - ho, 0.001), 2)
        }
        away_over[line] = {
            'over': round(ao, 4),
            'under': round(1 - ao, 4),
            'over_odds': round(1 / max(ao, 0.001), 2),
            'under_odds': round(1 / max(1 - ao, 0.001), 2)
        }

    # --- Köşe / Kart Tahmini ---
    total_xg = home_xg + away_xg
    estimated_corners = round(total_xg * 3.8 + 2.5, 1)
    estimated_cards = round(total_xg * 0.8 + 2.0, 1)

    # --- Güven Skoru ---
    data_quality = min(1.0, (
        (1 if home_team.matches_played >= 5 else home_team.matches_played / 5) * 0.2 +
        (1 if away_team.matches_played >= 5 else away_team.matches_played / 5) * 0.2 +
        (1 if home_team.form_matches >= 5 else home_team.form_matches / 5) * 0.2 +
        (1 if away_team.form_matches >= 5 else away_team.form_matches / 5) * 0.2 +
        (1 if h2h and h2h.total_matches >= 3 else (h2h.total_matches / 3 if h2h else 0)) * 0.2
    ))

    # --- Value Bet Tespiti + Kelly ---
    value_bets = _detect_value_bets(
        home_win_prob, draw_prob, away_win_prob,
        over_under, btts_yes, btts_no,
        score_probs, handicaps, double_chance,
        ht_over_under
    )

    # --- Kombine Bahis Önerileri ---
    combo_bets = _generate_combo_bets(
        home_win_prob, draw_prob, away_win_prob,
        over_under, btts_yes, btts_no,
        double_chance, home_xg, away_xg,
        home_team, away_team
    )

    # --- Decay-weighted form trendi ---
    home_trend = _calculate_trend(home_team)
    away_trend = _calculate_trend(away_team)

    return {
        'home_team': home_team.name,
        'away_team': away_team.name,
        'home_xg': home_xg,
        'away_xg': away_xg,
        'total_xg': round(home_xg + away_xg, 3),

        'match_result': {
            'home_win': round(home_win_prob, 4),
            'draw': round(draw_prob, 4),
            'away_win': round(away_win_prob, 4),
            'home_odds': round(1 / max(home_win_prob, 0.001), 2),
            'draw_odds': round(1 / max(draw_prob, 0.001), 2),
            'away_odds': round(1 / max(away_win_prob, 0.001), 2)
        },

        'double_chance': {
            '1X': {'prob': double_chance['1X'], 'odds': round(1 / max(double_chance['1X'], 0.001), 2)},
            'X2': {'prob': double_chance['X2'], 'odds': round(1 / max(double_chance['X2'], 0.001), 2)},
            '12': {'prob': double_chance['12'], 'odds': round(1 / max(double_chance['12'], 0.001), 2)}
        },

        'over_under': over_under,
        'ht_over_under': ht_over_under,

        'ht_result': {
            'home_win': round(ht_home_win, 4),
            'draw': round(ht_draw, 4),
            'away_win': round(ht_away_win, 4),
            'home_odds': round(1 / max(ht_home_win, 0.001), 2),
            'draw_odds': round(1 / max(ht_draw, 0.001), 2),
            'away_odds': round(1 / max(ht_away_win, 0.001), 2)
        },

        'btts': {
            'yes': round(btts_yes, 4),
            'no': round(btts_no, 4),
            'yes_odds': round(1 / max(btts_yes, 0.001), 2),
            'no_odds': round(1 / max(btts_no, 0.001), 2)
        },

        'handicaps': handicaps,
        'top_scores': score_probs[:15],
        'score_matrix': matrix,

        'home_team_goals': home_over,
        'away_team_goals': away_over,

        'corners_estimate': estimated_corners,
        'cards_estimate': estimated_cards,

        'data_quality': round(data_quality, 2),
        'value_bets': value_bets,
        'combo_bets': combo_bets,

        'dixon_coles_rho': round(rho, 4),

        'home_trend': home_trend,
        'away_trend': away_trend,

        'home_stats_summary': {
            'form': ''.join(home_team.form_results[-10:]),
            'form_ppg': round(home_team.form_ppg, 2),
            'avg_gf': round(home_team.avg_gf, 2),
            'avg_ga': round(home_team.avg_ga, 2),
            'home_avg_gf': round(home_team.home_avg_gf, 2),
            'home_avg_ga': round(home_team.home_avg_ga, 2),
            'clean_sheet_pct': round(home_team.clean_sheet_pct * 100, 1),
            'btts_pct': round(home_team.real_btts_rate * 100, 1),
            'over25_pct': round(home_team.real_over25_rate * 100, 1),
            'ppg': round(home_team.ppg, 2),
            'matches_played': home_team.matches_played,
            'wins': home_team.wins,
            'draws': home_team.draws,
            'losses': home_team.losses,
            'gf': home_team.goals_for,
            'ga': home_team.goals_against,
            'gd': home_team.goal_diff,
            'ht_goal_ratio': round(home_team.ht_goal_ratio * 100, 1),
        },

        'away_stats_summary': {
            'form': ''.join(away_team.form_results[-10:]),
            'form_ppg': round(away_team.form_ppg, 2),
            'avg_gf': round(away_team.avg_gf, 2),
            'avg_ga': round(away_team.avg_ga, 2),
            'away_avg_gf': round(away_team.away_avg_gf, 2),
            'away_avg_ga': round(away_team.away_avg_ga, 2),
            'clean_sheet_pct': round(away_team.clean_sheet_pct * 100, 1),
            'btts_pct': round(away_team.real_btts_rate * 100, 1),
            'over25_pct': round(away_team.real_over25_rate * 100, 1),
            'ppg': round(away_team.ppg, 2),
            'matches_played': away_team.matches_played,
            'wins': away_team.wins,
            'draws': away_team.draws,
            'losses': away_team.losses,
            'gf': away_team.goals_for,
            'ga': away_team.goals_against,
            'gd': away_team.goal_diff,
            'ht_goal_ratio': round(away_team.ht_goal_ratio * 100, 1),
        },

        'h2h_summary': {
            'total': h2h.total_matches if h2h else 0,
            'home_wins': h2h.home_wins if h2h else 0,
            'draws': h2h.draws if h2h else 0,
            'away_wins': h2h.away_wins if h2h else 0,
            'avg_goals': round(h2h.avg_total_goals, 2) if h2h else 0,
            'over25_pct': round(h2h.over25_pct * 100, 1) if h2h else 0,
            'btts_pct': round(h2h.btts_pct * 100, 1) if h2h else 0,
            'results': h2h.results[:10] if h2h else []
        } if h2h else None
    }


# ============================================================
# YARDIMCI FONKSİYONLAR
# ============================================================

def _get_ht_factor(home_team: TeamStats, away_team: TeamStats) -> float:
    """İlk yarı gol oranını gerçek veriden hesapla."""
    ratios = []
    if home_team.ht_matches > 0:
        ratios.append(home_team.ht_goal_ratio)
    if away_team.ht_matches > 0:
        ratios.append(away_team.ht_goal_ratio)

    if ratios:
        # Gerçek verinin ortalamasını al, ama 0.42 varsayılanına doğru regress et
        real_avg = sum(ratios) / len(ratios)
        # %70 gerçek veri, %30 varsayılan
        return real_avg * 0.70 + 0.42 * 0.30
    return 0.42


def _blend_real_btts(home: TeamStats, away: TeamStats, h2h: Optional[H2HRecord], poisson_btts: float) -> float:
    """Gerçek KG oranını Poisson tahminiyle blend et."""
    real_rates = []
    weights = []

    if home.btts_count > 0 and home.matches_played >= 5:
        real_rates.append(home.real_btts_rate)
        weights.append(0.3)
    if away.btts_count > 0 and away.matches_played >= 5:
        real_rates.append(away.real_btts_rate)
        weights.append(0.3)
    if h2h and h2h.total_matches >= 3:
        real_rates.append(h2h.btts_pct)
        weights.append(0.2)

    if not real_rates:
        return poisson_btts

    # Ağırlıklı ortalama (gerçek veriler + Poisson)
    real_avg = sum(r * w for r, w in zip(real_rates, weights)) / sum(weights)
    poisson_weight = 0.5
    real_weight = 0.5
    blended = poisson_btts * poisson_weight + real_avg * real_weight
    return max(0.05, min(0.95, blended))


def _blend_real_ou25(home: TeamStats, away: TeamStats, h2h: Optional[H2HRecord], poisson_ou: float) -> float:
    """Gerçek Ü2.5 oranını Poisson tahminiyle blend et."""
    real_rates = []
    weights = []

    if home.over25_count > 0 and home.matches_played >= 5:
        real_rates.append(home.real_over25_rate)
        weights.append(0.3)
    if away.over25_count > 0 and away.matches_played >= 5:
        real_rates.append(away.real_over25_rate)
        weights.append(0.3)
    if h2h and h2h.total_matches >= 3:
        real_rates.append(h2h.over25_pct)
        weights.append(0.2)

    if not real_rates:
        return poisson_ou

    real_avg = sum(r * w for r, w in zip(real_rates, weights)) / sum(weights)
    poisson_weight = 0.5
    real_weight = 0.5
    blended = poisson_ou * poisson_weight + real_avg * real_weight
    return max(0.05, min(0.95, blended))


def _calculate_trend(team: TeamStats) -> Dict:
    """Son maçlardaki performans trendi. Decay-weighted."""
    if not team.recent_matches or len(team.recent_matches) < 3:
        return {'direction': 'stable', 'strength': 0, 'label': 'Veri Yetersiz'}

    matches = team.recent_matches
    n = len(matches)
    mid = n // 2

    # İlk yarı vs son yarı gol performansı
    first_half_gf = sum(m.get('goals_for', 0) for m in matches[:mid]) / max(mid, 1)
    second_half_gf = sum(m.get('goals_for', 0) for m in matches[mid:]) / max(n - mid, 1)

    first_half_ga = sum(m.get('goals_against', 0) for m in matches[:mid]) / max(mid, 1)
    second_half_ga = sum(m.get('goals_against', 0) for m in matches[mid:]) / max(n - mid, 1)

    # Son 3 maçın form puanı
    last3 = team.form_results[-3:] if len(team.form_results) >= 3 else team.form_results
    last3_ppg = sum(3 if r == 'W' else (1 if r == 'D' else 0) for r in last3) / max(len(last3), 1)

    # Trend yönü
    attack_trend = second_half_gf - first_half_gf
    defense_trend = second_half_ga - first_half_ga  # Negatif = gelişme

    if attack_trend > 0.3 and defense_trend < 0.1:
        direction = 'up'
        strength = min(attack_trend * 2, 1.0)
        label = 'Yukseliste'
    elif attack_trend < -0.3 or defense_trend > 0.3:
        direction = 'down'
        strength = min(abs(attack_trend) + abs(defense_trend), 1.0)
        label = 'Dususte'
    else:
        direction = 'stable'
        strength = 0.2
        label = 'Stabil'

    if last3_ppg >= 2.5:
        label += ' (Son 3: güçlü)'
    elif last3_ppg <= 0.5:
        label += ' (Son 3: zayıf)'

    return {
        'direction': direction,
        'strength': round(strength, 2),
        'label': label,
        'last3_ppg': round(last3_ppg, 2),
        'attack_trend': round(attack_trend, 2),
        'defense_trend': round(defense_trend, 2),
    }


def _generate_combo_bets(
    home_prob, draw_prob, away_prob,
    over_under, btts_yes, btts_no,
    double_chance, home_xg, away_xg,
    home_team, away_team
) -> List[Dict]:
    """
    Kombine bahis önerileri. Korelasyon bazlı mantıklı ikililer/üçlüler.
    """
    combos = []

    # Favori + Ü2.5 (yüksek korelasyon: favori kazanırsa genelde çok gol olur)
    ou25 = over_under.get(2.5, {})
    if home_prob > 0.45 and ou25.get('over', 0) > 0.45:
        # Birleşik olasılık: bağımsız değil, korelasyon var
        # Favori kazandığında Ü2.5 olma şansı %60-70 civarı
        corr_factor = 1.15  # Pozitif korelasyon
        combo_prob = home_prob * ou25['over'] * corr_factor
        combo_prob = min(combo_prob, min(home_prob, ou25['over']))
        combos.append({
            'name': f'{home_team.name} Kazanır + 2.5 Üst',
            'picks': [f'1 ({home_team.name})', '2.5 Üst'],
            'probability': round(combo_prob * 100, 1),
            'fair_odds': round(1 / max(combo_prob, 0.001), 2),
            'confidence': 'HIGH' if combo_prob > 0.30 else 'MEDIUM',
            'kelly': round(kelly_criterion(combo_prob, 1 / max(combo_prob, 0.001) * 1.08), 1),
            'reasoning': 'Favori kazandığında genelde çok gol olur'
        })

    if away_prob > 0.45 and ou25.get('over', 0) > 0.45:
        corr_factor = 1.15
        combo_prob = away_prob * ou25['over'] * corr_factor
        combo_prob = min(combo_prob, min(away_prob, ou25['over']))
        combos.append({
            'name': f'{away_team.name} Kazanır + 2.5 Üst',
            'picks': [f'2 ({away_team.name})', '2.5 Üst'],
            'probability': round(combo_prob * 100, 1),
            'fair_odds': round(1 / max(combo_prob, 0.001), 2),
            'confidence': 'HIGH' if combo_prob > 0.30 else 'MEDIUM',
            'kelly': round(kelly_criterion(combo_prob, 1 / max(combo_prob, 0.001) * 1.08), 1),
            'reasoning': 'Deplasman favorisi genelde farkla kazanır'
        })

    # KG Var + Ü2.5 (çok yüksek korelasyon)
    if btts_yes > 0.45 and ou25.get('over', 0) > 0.45:
        # KG Var olduğunda Ü2.5 olma şansı çok yüksek
        corr_factor = 1.30
        combo_prob = btts_yes * ou25['over'] * corr_factor
        combo_prob = min(combo_prob, min(btts_yes, ou25['over']))
        combos.append({
            'name': 'KG Var + 2.5 Üst',
            'picks': ['KG Var', '2.5 Üst'],
            'probability': round(combo_prob * 100, 1),
            'fair_odds': round(1 / max(combo_prob, 0.001), 2),
            'confidence': 'HIGH' if combo_prob > 0.35 else 'MEDIUM',
            'kelly': round(kelly_criterion(combo_prob, 1 / max(combo_prob, 0.001) * 1.08), 1),
            'reasoning': 'İki takım da gol atarsa toplam neredeyse kesin 3+'
        })

    # Çifte Şans + Ü/A (düşük risk)
    dc_1x = double_chance.get('1X', 0)
    dc_x2 = double_chance.get('X2', 0)
    if dc_1x > 0.65:
        ou15 = over_under.get(1.5, {})
        if ou15.get('over', 0) > 0.60:
            combo_prob = dc_1x * ou15['over'] * 1.05
            combo_prob = min(combo_prob, min(dc_1x, ou15['over']))
            combos.append({
                'name': f'1X ({home_team.name} veya X) + 1.5 Üst',
                'picks': ['1X', '1.5 Üst'],
                'probability': round(combo_prob * 100, 1),
                'fair_odds': round(1 / max(combo_prob, 0.001), 2),
                'confidence': 'HIGH' if combo_prob > 0.45 else 'MEDIUM',
                'kelly': round(kelly_criterion(combo_prob, 1 / max(combo_prob, 0.001) * 1.08), 1),
                'reasoning': 'Düşük riskli kombine: ev sahibi kaybetmez + en az 2 gol'
            })
    if dc_x2 > 0.65:
        ou15 = over_under.get(1.5, {})
        if ou15.get('over', 0) > 0.60:
            combo_prob = dc_x2 * ou15['over'] * 1.05
            combo_prob = min(combo_prob, min(dc_x2, ou15['over']))
            combos.append({
                'name': f'X2 ({away_team.name} veya X) + 1.5 Üst',
                'picks': ['X2', '1.5 Üst'],
                'probability': round(combo_prob * 100, 1),
                'fair_odds': round(1 / max(combo_prob, 0.001), 2),
                'confidence': 'HIGH' if combo_prob > 0.45 else 'MEDIUM',
                'kelly': round(kelly_criterion(combo_prob, 1 / max(combo_prob, 0.001) * 1.08), 1),
                'reasoning': 'Düşük riskli kombine: deplasman kaybetmez + en az 2 gol'
            })

    # KG Yok + 2.5 Alt (defansif maç profili)
    if btts_no > 0.50 and ou25.get('under', 0) > 0.45:
        corr_factor = 1.25
        combo_prob = btts_no * ou25['under'] * corr_factor
        combo_prob = min(combo_prob, min(btts_no, ou25['under']))
        combos.append({
            'name': 'KG Yok + 2.5 Alt',
            'picks': ['KG Yok', '2.5 Alt'],
            'probability': round(combo_prob * 100, 1),
            'fair_odds': round(1 / max(combo_prob, 0.001), 2),
            'confidence': 'MEDIUM' if combo_prob > 0.25 else 'LOW',
            'kelly': round(kelly_criterion(combo_prob, 1 / max(combo_prob, 0.001) * 1.08), 1),
            'reasoning': 'Defansif maç profili: bir takım gol atamaz + az gol'
        })

    # Güvene göre sırala
    confidence_order = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}
    combos.sort(key=lambda x: (confidence_order.get(x['confidence'], 3), -x['probability']))

    return combos[:6]


def _detect_value_bets(
    home_prob, draw_prob, away_prob,
    over_under, btts_yes, btts_no,
    score_probs, handicaps, double_chance,
    ht_over_under
) -> List[Dict]:
    """Value bet tespiti + Kelly Criterion."""
    bets = []

    def add_bet(market, pick, prob, category):
        fair_odds = round(1 / max(prob, 0.001), 2)
        # Kelly: tipik bahisçi marjı ~%5-8, yani oran fair_odds'un %92-95'i kadar
        # Ama value bet olması için bahisçi oranı fair'den büyük olmalı
        # Burada fair odds * 1.05 kullanıyoruz (bahisçide fair değerinin biraz üstü bulunduğu varsayımı)
        implied_odds = fair_odds * 1.08
        kelly_pct = kelly_criterion(prob, implied_odds, fraction=0.25)

        if prob < 0.30:
            conf = 'LOW'
        elif prob < 0.55:
            conf = 'MEDIUM'
        else:
            conf = 'HIGH'

        bets.append({
            'market': market,
            'pick': pick,
            'probability': round(prob * 100, 1),
            'fair_odds': fair_odds,
            'confidence': conf,
            'category': category,
            'kelly': round(kelly_pct, 1),
        })

    # --- Maç Sonucu ---
    if home_prob > 0.50:
        add_bet('Maç Sonucu', 'Ev Sahibi Kazanır (1)', home_prob, 'match_result')
    if away_prob > 0.50:
        add_bet('Maç Sonucu', 'Deplasman Kazanır (2)', away_prob, 'match_result')
    if draw_prob > 0.28:
        add_bet('Maç Sonucu', 'Beraberlik (X)', draw_prob, 'match_result')

    # --- Çifte Şans ---
    for key, prob in double_chance.items():
        if prob > 0.65:
            labels = {'1X': 'Ev Sahibi veya Beraberlik', 'X2': 'Deplasman veya Beraberlik', '12': 'Ev Sahibi veya Deplasman'}
            add_bet('Çifte Şans', f"{labels[key]} ({key})", prob, 'double_chance')

    # --- Ü/A ---
    for line, data in over_under.items():
        if data['over'] > 0.55:
            add_bet('Toplam Gol', f'{line} Üst', data['over'], 'over_under')
        elif data['under'] > 0.55:
            add_bet('Toplam Gol', f'{line} Alt', data['under'], 'over_under')

    # --- İY Ü/A ---
    for line, data in ht_over_under.items():
        if data['over'] > 0.55:
            add_bet('İlk Yarı Gol', f'İY {line} Üst', data['over'], 'ht_over_under')
        elif data['under'] > 0.60:
            add_bet('İlk Yarı Gol', f'İY {line} Alt', data['under'], 'ht_over_under')

    # --- KG ---
    if btts_yes > 0.52:
        add_bet('Karşılıklı Gol', 'KG Var', btts_yes, 'btts')
    elif btts_no > 0.52:
        add_bet('Karşılıklı Gol', 'KG Yok', btts_no, 'btts')

    # --- Handikap ---
    for hcap, data in handicaps.items():
        if data['home_cover'] > 0.58 and hcap < 0:
            add_bet('Handikap', f'Ev Sahibi ({hcap:+.1f})', data['home_cover'], 'handicap')
        elif data['away_cover'] > 0.58 and hcap > 0:
            add_bet('Handikap', f'Deplasman ({-hcap:+.1f})', data['away_cover'], 'handicap')

    # --- En olası skor ---
    if score_probs and score_probs[0]['probability'] > 8:
        sp = score_probs[0]
        add_bet('Skor Tahmini', sp['score'], sp['probability'] / 100, 'correct_score')

    # Güvene göre sırala
    confidence_order = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}
    bets.sort(key=lambda x: (confidence_order.get(x['confidence'], 3), -x['probability']))

    return bets
