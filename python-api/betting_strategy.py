"""
競馬AI - 購入推奨・資金管理モジュール (FastAPI版)
Streamlit 3_予測_batch.py の機能を完全移植
"""

import math
from typing import Dict, List, Tuple, Optional
from itertools import combinations, permutations
import numpy as np
from datetime import datetime


class ProBettingStrategy:
    """プロ資金管理戦略"""
    
    def __init__(self, bankroll: int, risk_mode: str = 'balanced'):
        """
        Args:
            bankroll: 総資金
            risk_mode: 'conservative'(2%), 'balanced'(3.5%), 'aggressive'(5%)
        """
        self.bankroll = bankroll
        self.risk_rates = {
            'conservative': 0.02,
            'balanced': 0.035,
            'aggressive': 0.05
        }
        self.risk_rate = self.risk_rates.get(risk_mode, 0.035)
        self.per_race_limit = int(bankroll * self.risk_rate)
    
    def calculate_kelly_bet(
        self, 
        probability: float, 
        odds: float, 
        kelly_fraction: float = 0.25
    ) -> int:
        """
        ケリー基準で最適賭け額計算
        
        Args:
            probability: 的中確率 (0-1)
            odds: オッズ
            kelly_fraction: フラクショナルケリー (デフォルト1/4)
        
        Returns:
            推奨賭け額
        """
        if probability <= 0 or odds <= 1.0:
            return 0
        
        # ケリー基準: f* = (p × odds - 1) / (odds - 1)
        kelly_percentage = (probability * odds - 1) / (odds - 1)
        
        if kelly_percentage <= 0:
            return 0
        
        # フラクショナルケリー適用
        adjusted_kelly = kelly_percentage * kelly_fraction
        
        # 破産リスク回避: 上限5%
        adjusted_kelly = min(adjusted_kelly, 0.05)
        
        return int(self.bankroll * adjusted_kelly)
    
    def evaluate_race_level(
        self, 
        pro_eval: Optional[Dict], 
        best_bet: Dict, 
        min_ev: float = 1.2
    ) -> str:
        """
        レースレベル判定（skip/normal/decisive）
        
        Args:
            pro_eval: プロ戦略スコア評価結果
            best_bet: ベスト馬券種情報
            min_ev: 最低期待値フィルタ
        
        Returns:
            'skip', 'normal', 'decisive'
        """
        # 見送り判定
        if pro_eval and pro_eval.get('recommended_action') == '見送り':
            return 'skip'
        
        max_ev = best_bet.get('最大期待値', 0)
        if max_ev < min_ev:
            return 'skip'
        
        # 勝負レース判定
        difficulty_score = pro_eval.get('difficulty_score', 0) if pro_eval else 0
        max_prob = best_bet.get('最高確率', 0)
        
        is_decisive = (
            difficulty_score >= 0.7 or
            (max_ev >= 4.0 and max_prob >= 0.25) or
            max_ev >= 6.0
        )
        
        return 'decisive' if is_decisive else 'normal'
    
    def calculate_optimal_unit_price(
        self, 
        race_level: str, 
        dynamic_unit: bool = True
    ) -> int:
        """
        レベル別最適単価計算
        
        Args:
            race_level: 'skip', 'normal', 'decisive'
            dynamic_unit: 動的単価調整有効化
        
        Returns:
            推奨単価
        """
        if not dynamic_unit:
            return 100
        
        if race_level == 'skip':
            return 100
        elif race_level == 'decisive':
            if self.per_race_limit >= 5000:
                return 1000
            elif self.per_race_limit >= 3000:
                return 500
            else:
                return 200
        else:  # normal
            return 200 if self.per_race_limit >= 3000 else 100
    
    def get_budget_allocation(self, race_level: str) -> float:
        """
        レベル別予算配分比率
        
        Args:
            race_level: 'skip', 'normal', 'decisive'
        
        Returns:
            配分比率 (0-1)
        """
        allocation = {
            'skip': 0.0,
            'normal': 0.4,
            'decisive': 0.8
        }
        return allocation.get(race_level, 0.4)
    
    def calculate_purchase_count(
        self, 
        race_level: str, 
        unit_price: int, 
        bet_type: str
    ) -> int:
        """
        推奨購入点数計算
        
        Args:
            race_level: レースレベル
            unit_price: 単価
            bet_type: 馬券種
        
        Returns:
            推奨点数
        """
        allocation_rate = self.get_budget_allocation(race_level)
        budget = int(self.per_race_limit * allocation_rate)
        
        if budget <= 0:
            return 0
        
        # 馬券種別基本点数
        base_counts = {
            '単勝': 3,
            '馬連': 10,
            'ワイド': 10,
            '三連複': 10,
            '馬単': 20,
            '三連単': 30
        }
        
        base_count = base_counts.get(bet_type, 10)
        max_count = min(base_count, budget // unit_price)
        
        return max(1, max_count)


class BettingCombinationGenerator:
    """馬券組み合わせ生成"""
    
    @staticmethod
    def generate_tansho(predictions: List[Dict], top_n: int = 3) -> List[Dict]:
        """単勝候補生成"""
        sorted_preds = sorted(predictions, key=lambda x: x['expected_value'], reverse=True)
        candidates = []
        
        for horse in sorted_preds[:top_n]:
            candidates.append({
                'combination': str(horse['horse_no']),
                'expected_value': horse['expected_value'],
                'probability': horse['win_probability'],
                'odds': horse['odds']
            })
        
        return candidates
    
    @staticmethod
    def generate_umaren(predictions: List[Dict], top_n: int = 5) -> List[Dict]:
        """馬連候補生成"""
        sorted_preds = sorted(predictions, key=lambda x: x['expected_value'], reverse=True)
        top_horses = sorted_preds[:top_n]
        
        candidates = []
        for h1, h2 in combinations(top_horses, 2):
            combo = f"{h1['horse_no']}-{h2['horse_no']}"
            # 期待値は積形式（簡易計算）
            ev = (h1['expected_value'] + h2['expected_value']) / 2
            prob = h1['win_probability'] * h2['win_probability']
            
            candidates.append({
                'combination': combo,
                'expected_value': ev,
                'probability': prob
            })
        
        return sorted(candidates, key=lambda x: x['expected_value'], reverse=True)
    
    @staticmethod
    def generate_wide(predictions: List[Dict], top_n: int = 5) -> List[Dict]:
        """ワイド候補生成（馬連と同様）"""
        return BettingCombinationGenerator.generate_umaren(predictions, top_n)
    
    @staticmethod
    def generate_sanrenpuku(predictions: List[Dict], top_n: int = 5) -> List[Dict]:
        """三連複候補生成"""
        sorted_preds = sorted(predictions, key=lambda x: x['expected_value'], reverse=True)
        top_horses = sorted_preds[:top_n]
        
        candidates = []
        for h1, h2, h3 in combinations(top_horses, 3):
            combo = f"{h1['horse_no']}-{h2['horse_no']}-{h3['horse_no']}"
            ev = (h1['expected_value'] + h2['expected_value'] + h3['expected_value']) / 3
            prob = h1['win_probability'] * h2['win_probability'] * h3['win_probability']
            
            candidates.append({
                'combination': combo,
                'expected_value': ev,
                'probability': prob
            })
        
        return sorted(candidates, key=lambda x: x['expected_value'], reverse=True)
    
    @staticmethod
    def generate_umatan(predictions: List[Dict], top_n: int = 5) -> List[Dict]:
        """馬単候補生成"""
        sorted_preds = sorted(predictions, key=lambda x: x['expected_value'], reverse=True)
        top_horses = sorted_preds[:top_n]
        
        candidates = []
        for h1, h2 in permutations(top_horses, 2):
            combo = f"{h1['horse_no']}→{h2['horse_no']}"
            ev = (h1['expected_value'] + h2['expected_value']) / 2
            prob = h1['win_probability'] * h2['win_probability'] * 0.5  # 順序考慮
            
            candidates.append({
                'combination': combo,
                'expected_value': ev,
                'probability': prob
            })
        
        return sorted(candidates, key=lambda x: x['expected_value'], reverse=True)[:20]
    
    @staticmethod
    def generate_sanrentan(predictions: List[Dict], top_n: int = 5) -> List[Dict]:
        """三連単候補生成"""
        sorted_preds = sorted(predictions, key=lambda x: x['expected_value'], reverse=True)
        top_horses = sorted_preds[:top_n]
        
        candidates = []
        for h1, h2, h3 in permutations(top_horses, 3):
            combo = f"{h1['horse_no']}→{h2['horse_no']}→{h3['horse_no']}"
            ev = (h1['expected_value'] + h2['expected_value'] + h3['expected_value']) / 3
            prob = h1['win_probability'] * h2['win_probability'] * h3['win_probability'] * 0.3
            
            candidates.append({
                'combination': combo,
                'expected_value': ev,
                'probability': prob
            })
        
        return sorted(candidates, key=lambda x: x['expected_value'], reverse=True)[:30]


class RaceAnalyzer:
    """レース分析・評価"""
    
    @staticmethod
    def calculate_difficulty_score(predictions: List[Dict]) -> float:
        """
        レース難易度スコア計算（予測分布の偏り度）
        
        Args:
            predictions: 予測結果リスト
        
        Returns:
            難易度スコア (0-1, 高いほど予測しやすい)
        """
        if not predictions:
            return 0.0
        
        probs = [p['win_probability'] for p in predictions]
        
        # 標準偏差ベース（高いほど偏りが大きい = 予測しやすい）
        std_dev = np.std(probs)
        
        # 最大確率が突出しているかチェック
        max_prob = max(probs)
        avg_prob = np.mean(probs)
        
        # Gini係数的な偏り度
        concentration = max_prob / (avg_prob + 1e-6)
        
        # スコア統合（0-1に正規化）
        difficulty = min(1.0, (std_dev * 10 + concentration / 5) / 2)
        
        return difficulty
    
    @staticmethod
    def detect_nakaana_chance(predictions: List[Dict]) -> Optional[Dict]:
        """
        中穴チャンス検出（4-9番人気でオッズ断層）
        
        Args:
            predictions: 予測結果リスト
        
        Returns:
            中穴情報 or None
        """
        sorted_by_odds = sorted(predictions, key=lambda x: x['odds'])
        
        for i, horse in enumerate(sorted_by_odds[3:9], start=4):
            # 期待値が高く、オッズが美味しい
            if horse['expected_value'] >= 2.5 and horse['odds'] >= 8.0:
                return {
                    'horse_no': horse['horse_no'],
                    'horse_name': horse.get('horse_name', ''),
                    'odds': horse['odds'],
                    'expected_value': horse['expected_value'],
                    'popularity': i
                }
        
        return None
    
    @staticmethod
    def get_season_bonus(date_str: str) -> float:
        """
        シーズンボーナス計算
        
        Args:
            date_str: 日付文字列 (YYYY-MM-DD)
        
        Returns:
            ボーナス率 (1.0 = 標準, 1.1 = +10%)
        """
        try:
            date = datetime.strptime(date_str, '%Y-%m-%d')
            month = date.month
            
            if 3 <= month <= 5:  # 春競馬
                return 1.10
            elif 6 <= month <= 8:  # 夏競馬
                return 0.90
            elif 9 <= month <= 11:  # 秋競馬
                return 1.05
            else:  # 冬競馬
                return 1.00
        except:
            return 1.00
    
    @staticmethod
    def check_high_recovery_jockeys(predictions: List[Dict], top_n: int = 3) -> Dict:
        """
        高回収率騎手チェック
        
        Args:
            predictions: 予測結果リスト
            top_n: 上位何頭をチェックするか
        
        Returns:
            騎手ボーナス情報
        """
        # 高回収率騎手マスタ（仮データ、本来はDBから取得）
        high_recovery_jockeys = {
            '武豊': 1.25,
            '川田将雅': 1.22,
            'C.ルメール': 1.28,
            '横山武史': 1.18,
            '福永祐一': 1.20
        }
        
        sorted_preds = sorted(predictions, key=lambda x: x['expected_value'], reverse=True)
        top_horses = sorted_preds[:top_n]
        
        found_jockeys = []
        bonus = 1.0
        
        for horse in top_horses:
            jockey = horse.get('jockey_name', '')
            if jockey in high_recovery_jockeys:
                found_jockeys.append({
                    'jockey': jockey,
                    'recovery_rate': high_recovery_jockeys[jockey],
                    'horse_no': horse['horse_no']
                })
                bonus = 1.15  # +15%ボーナス
        
        return {
            'has_high_recovery_jockey': len(found_jockeys) > 0,
            'jockeys': found_jockeys,
            'bonus': bonus
        }
    
    @staticmethod
    def evaluate_pro_strategy(
        predictions: List[Dict], 
        race_info: Dict
    ) -> Dict:
        """
        プロ戦略スコア総合評価
        
        Args:
            predictions: 予測結果リスト
            race_info: レース情報
        
        Returns:
            評価結果辞書
        """
        difficulty_score = RaceAnalyzer.calculate_difficulty_score(predictions)
        nakaana = RaceAnalyzer.detect_nakaana_chance(predictions)
        season_bonus = RaceAnalyzer.get_season_bonus(race_info.get('date', '2026-01-01'))
        jockey_info = RaceAnalyzer.check_high_recovery_jockeys(predictions)
        
        # 総合推奨アクション決定
        max_ev = max([p['expected_value'] for p in predictions], default=0)
        
        if difficulty_score >= 0.7 and max_ev >= 3.0:
            recommended_action = '勝負'
        elif max_ev < 1.2:
            # 期待値が低すぎる場合は見送り
            recommended_action = '見送り'
        elif difficulty_score < 0.15 and max_ev < 1.5:
            # 予測が分散していて期待値も低い場合のみ見送り（閾値を0.3→0.15に緩和）
            recommended_action = '見送り'
        else:
            recommended_action = '通常'
        
        return {
            'difficulty_score': round(difficulty_score, 3),
            'recommended_action': recommended_action,
            'nakaana_chance': nakaana,
            'season_bonus': season_bonus,
            'jockey_bonus': jockey_info,
            'confidence_level': 'high' if difficulty_score >= 0.6 else 'medium' if difficulty_score >= 0.4 else 'low'
        }


class BettingRecommender:
    """購入推奨システム統合"""
    
    def __init__(
        self, 
        bankroll: int, 
        risk_mode: str = 'balanced',
        use_kelly: bool = True,
        dynamic_unit: bool = True,
        min_ev: float = 1.2
    ):
        self.strategy = ProBettingStrategy(bankroll, risk_mode)
        self.generator = BettingCombinationGenerator()
        self.analyzer = RaceAnalyzer()
        self.use_kelly = use_kelly
        self.dynamic_unit = dynamic_unit
        self.min_ev = min_ev
    
    def analyze_and_recommend(
        self, 
        predictions: List[Dict], 
        race_info: Dict
    ) -> Dict:
        """
        レース分析と購入推奨（メイン関数）
        
        Args:
            predictions: 予測結果リスト
                [{'horse_no': 1, 'win_probability': 0.25, 'odds': 4.5, ...}, ...]
            race_info: レース情報
                {'race_id': '202401010101', 'race_name': '...', 'date': '2024-01-01', ...}
        
        Returns:
            推奨情報辞書
        """
        # 期待値計算
        for pred in predictions:
            pred['expected_value'] = pred['win_probability'] * pred['odds']
        
        # プロ戦略評価
        pro_eval = self.analyzer.evaluate_pro_strategy(predictions, race_info)
        
        # 馬券種別候補生成
        bet_types = {
            '単勝': self.generator.generate_tansho(predictions),
            '馬連': self.generator.generate_umaren(predictions),
            'ワイド': self.generator.generate_wide(predictions),
            '三連複': self.generator.generate_sanrenpuku(predictions),
            '馬単': self.generator.generate_umatan(predictions),
            '三連単': self.generator.generate_sanrentan(predictions)
        }
        
        # ベスト馬券種選定
        best_bet_type, best_bet_info = self._select_best_bet_type(bet_types)
        
        # レースレベル判定
        race_level = self.strategy.evaluate_race_level(pro_eval, best_bet_info, self.min_ev)
        
        # 最適単価・点数計算
        unit_price = self.strategy.calculate_optimal_unit_price(race_level, self.dynamic_unit)
        purchase_count = self.strategy.calculate_purchase_count(race_level, unit_price, best_bet_type)
        
        # ケリー基準計算（オプション）
        kelly_amount = None
        if self.use_kelly:
            top_horse = max(predictions, key=lambda x: x['expected_value'])
            kelly_amount = self.strategy.calculate_kelly_bet(
                top_horse['win_probability'],
                top_horse['odds']
            )
        
        # 予算計算
        allocation_rate = self.strategy.get_budget_allocation(race_level)
        budget = int(self.strategy.per_race_limit * allocation_rate)
        total_cost = purchase_count * unit_price
        
        return {
            'race_info': race_info,
            'pro_evaluation': pro_eval,
            'predictions': sorted(predictions, key=lambda x: x['expected_value'], reverse=True),
            'bet_types': bet_types,
            'best_bet_type': best_bet_type,
            'best_bet_info': best_bet_info,
            'race_level': race_level,
            'recommendation': {
                'unit_price': unit_price,
                'purchase_count': purchase_count,
                'total_cost': total_cost,
                'budget': budget,
                'budget_usage_rate': round(total_cost / budget * 100, 1) if budget > 0 else 0,
                'kelly_recommended_amount': kelly_amount,
                'strategy_explanation': self._generate_strategy_explanation(
                    race_level, best_bet_type, purchase_count, unit_price, pro_eval
                )
            }
        }
    
    def _select_best_bet_type(self, bet_types: Dict[str, List[Dict]]) -> Tuple[str, Dict]:
        """ベスト馬券種選定"""
        best_type = None
        best_info = {
            '平均期待値': 0,
            '最大期待値': 0,
            '候補数': 0,
            '最高確率': 0
        }
        
        for bet_type, candidates in bet_types.items():
            if not candidates:
                continue
            
            evs = [c['expected_value'] for c in candidates]
            probs = [c['probability'] for c in candidates]
            
            avg_ev = np.mean(evs)
            max_ev = max(evs)
            
            # ベスト判定（最大期待値重視）
            if max_ev > best_info['最大期待値']:
                best_type = bet_type
                best_info = {
                    '平均期待値': round(avg_ev, 2),
                    '最大期待値': round(max_ev, 2),
                    '候補数': len(candidates),
                    '最高確率': round(max(probs), 4)
                }
        
        return best_type or '単勝', best_info
    
    def _generate_strategy_explanation(
        self, 
        race_level: str, 
        bet_type: str, 
        count: int, 
        unit_price: int, 
        pro_eval: Dict
    ) -> str:
        """戦略説明文生成"""
        level_text = {
            'skip': '見送り推奨',
            'normal': '通常レース',
            'decisive': '🔥 勝負レース！'
        }
        
        explanation = f"{level_text[race_level]} - {bet_type} {count}点 @¥{unit_price}\n"
        
        if race_level == 'skip':
            explanation += "期待値が低いため見送りを推奨します。"
        elif race_level == 'decisive':
            explanation += f"難易度スコア {pro_eval['difficulty_score']:.2f} - 高信頼度予測！"
            if pro_eval.get('nakaana_chance'):
                explanation += " 中穴チャンスあり。"
        else:
            explanation += "堅実な通常配分で購入推奨。"
        
        return explanation
