"""
Flask Backend — Maç Analiz API + Statik HTML sunucu.
"""

import os
import json
import traceback
from typing import Dict
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from analysis_engine import analyze_match, TeamStats, H2HRecord
from scraper import scrape_and_prepare

app = Flask(__name__, static_folder='static')
CORS(app)


@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/api/healthz')
def healthz():
    return jsonify({'status': 'ok', 'time': datetime.now().isoformat()})


@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)


@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    """
    Ana analiz endpoint'i.
    Body: {"urls": ["url1", "url2", ...]}
    veya
    Body: {"urls": [...], "manual": true, "home": {...}, "away": {...}, "h2h": {...}}
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'JSON body gerekli'}), 400

        urls = data.get('urls', [])
        is_manual = data.get('manual', False)

        if is_manual:
            # Manuel veri girişi
            home_data = data.get('home', {})
            away_data = data.get('away', {})
            h2h_data = data.get('h2h', {})

            home = _build_stats_from_manual(home_data)
            away = _build_stats_from_manual(away_data)
            h2h = _build_h2h_from_manual(h2h_data) if h2h_data else None
            league_avg = data.get('league_avg', 2.5)
            raw_data = {'source': 'manual'}

        elif urls:
            # URL'lerden scrape et
            home, away, h2h, league_avg, raw_data = scrape_and_prepare(urls)

        else:
            return jsonify({'error': 'URL veya manuel veri gerekli'}), 400

        # Analiz yap
        result = analyze_match(home, away, h2h, league_avg)

        # Ek bilgiler
        result['analysis_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        result['sources_used'] = list(raw_data.get('sources', {}).keys()) if isinstance(raw_data, dict) else ['manual']
        result['scrape_errors'] = raw_data.get('errors', []) if isinstance(raw_data, dict) else []

        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/quick-analyze', methods=['POST'])
def api_quick_analyze():
    """
    Hızlı analiz — sadece temel istatistikleri alıp analiz et.
    Manuel giriş kısayolu.
    """
    try:
        data = request.get_json()

        home = TeamStats(
            name=data.get('home_name', 'Ev Sahibi'),
            matches_played=data.get('home_mp', 10),
            wins=data.get('home_w', 5),
            draws=data.get('home_d', 3),
            losses=data.get('home_l', 2),
            goals_for=data.get('home_gf', 15),
            goals_against=data.get('home_ga', 10),
            home_played=data.get('home_hp', 5),
            home_wins=data.get('home_hw', 3),
            home_draws=data.get('home_hd', 1),
            home_losses=data.get('home_hl', 1),
            home_gf=data.get('home_hgf', 9),
            home_ga=data.get('home_hga', 5),
            away_played=data.get('home_ap', 5),
            away_wins=data.get('home_aw', 2),
            away_draws=data.get('home_ad', 2),
            away_losses=data.get('home_al', 1),
            away_gf=data.get('home_agf', 6),
            away_ga=data.get('home_aga', 5),
            form_results=list(data.get('home_form', 'WWDLW')),
            form_gf=data.get('home_fgf', 8),
            form_ga=data.get('home_fga', 4),
            form_matches=data.get('home_fm', 5),
        )

        away = TeamStats(
            name=data.get('away_name', 'Deplasman'),
            matches_played=data.get('away_mp', 10),
            wins=data.get('away_w', 4),
            draws=data.get('away_d', 2),
            losses=data.get('away_l', 4),
            goals_for=data.get('away_gf', 12),
            goals_against=data.get('away_ga', 14),
            home_played=data.get('away_hp', 5),
            home_wins=data.get('away_hw', 3),
            home_draws=data.get('away_hd', 1),
            home_losses=data.get('away_hl', 1),
            home_gf=data.get('away_hgf', 8),
            home_ga=data.get('away_hga', 5),
            away_played=data.get('away_ap', 5),
            away_wins=data.get('away_aw', 1),
            away_draws=data.get('away_ad', 1),
            away_losses=data.get('away_al', 3),
            away_gf=data.get('away_agf', 4),
            away_ga=data.get('away_aga', 9),
            form_results=list(data.get('away_form', 'LDWDL')),
            form_gf=data.get('away_fgf', 5),
            form_ga=data.get('away_fga', 7),
            form_matches=data.get('away_fm', 5),
        )

        h2h = None
        if data.get('h2h_total', 0) > 0:
            h2h = H2HRecord(
                total_matches=data.get('h2h_total', 0),
                home_wins=data.get('h2h_hw', 0),
                draws=data.get('h2h_d', 0),
                away_wins=data.get('h2h_aw', 0),
                home_goals=data.get('h2h_hg', 0),
                away_goals=data.get('h2h_ag', 0),
            )

        league_avg = data.get('league_avg', 2.5)
        result = analyze_match(home, away, h2h, league_avg)
        result['analysis_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def _build_stats_from_manual(data: Dict) -> TeamStats:
    """Manuel girişten TeamStats oluştur."""
    return TeamStats(
        name=data.get('name', ''),
        matches_played=data.get('matches_played', 0),
        wins=data.get('wins', 0),
        draws=data.get('draws', 0),
        losses=data.get('losses', 0),
        goals_for=data.get('goals_for', 0),
        goals_against=data.get('goals_against', 0),
        home_played=data.get('home_played', 0),
        home_wins=data.get('home_wins', 0),
        home_draws=data.get('home_draws', 0),
        home_losses=data.get('home_losses', 0),
        home_gf=data.get('home_gf', 0),
        home_ga=data.get('home_ga', 0),
        away_played=data.get('away_played', 0),
        away_wins=data.get('away_wins', 0),
        away_draws=data.get('away_draws', 0),
        away_losses=data.get('away_losses', 0),
        away_gf=data.get('away_gf', 0),
        away_ga=data.get('away_ga', 0),
        form_results=list(data.get('form', '')),
        form_gf=data.get('form_gf', 0),
        form_ga=data.get('form_ga', 0),
        form_matches=data.get('form_matches', 0),
    )


def _build_h2h_from_manual(data: Dict) -> H2HRecord:
    return H2HRecord(
        total_matches=data.get('total', 0),
        home_wins=data.get('home_wins', 0),
        draws=data.get('draws', 0),
        away_wins=data.get('away_wins', 0),
        home_goals=data.get('home_goals', 0),
        away_goals=data.get('away_goals', 0),
    )


if __name__ == '__main__':
    os.makedirs('static', exist_ok=True)
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    print("=" * 60)
    print("  GOL ANALİZ SİSTEMİ")
    print(f"  http://0.0.0.0:{port}")
    print("=" * 60)
    app.run(host='0.0.0.0', port=port, debug=debug)
