"""
Optunaを使用したLightGBMハイパーパラメータ最適化

競馬予測モデルのハイパーパラメータを自動最適化します。
"""

import optuna
from optuna.samplers import TPESampler
from optuna.pruners import MedianPruner
import lightgbm as lgb
from sklearn.model_selection import StratifiedKFold, KFold
from sklearn.metrics import roc_auc_score, mean_squared_error
import numpy as np
import pandas as pd
from typing import Dict, Any, Tuple, Optional
import warnings
import logging
import time

from .gpu_utils import (
    append_gpu_usage_log,
    append_optuna_trial_log,
    detect_lightgbm_device,
)

warnings.filterwarnings('ignore')

# ロガー設定
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


class OptunaLightGBMOptimizer:
    """
    OptunaによるLightGBMハイパーパラメータ最適化クラス
    
    競馬予測に特化したパラメータ探索を行います。
    """
    
    def __init__(
        self,
        n_trials: int = 5,
        cv_folds: int = 5,
        random_state: int = 42,
        direction: str = "maximize",  # AUCを最大化
        sampler: Optional[optuna.samplers.BaseSampler] = None,
        pruner: Optional[optuna.pruners.BasePruner] = None,
        timeout: Optional[int] = None,  # 秒単位のタイムアウト
        show_progress: bool = True,
        is_regression: bool = False,  # True の場合 KFold + neg_RMSE で最適化
        device_type: str = "cpu",  # "cpu" or "gpu"
        fixed_boosting_type: Optional[str] = None,
        n_jobs: int = 1,
        gc_after_trial: bool = True,
    ):
        """
        Parameters
        ----------
        n_trials : int, default=100
            最適化の試行回数
        cv_folds : int, default=5
            クロスバリデーションのフォールド数
        random_state : int, default=42
            乱数シード
        direction : str, default="maximize"
            最適化方向（"maximize" or "minimize"）
        sampler : optuna.samplers.BaseSampler, optional
            サンプリング戦略（デフォルト: TPESampler）
        pruner : optuna.pruners.BasePruner, optional
            枝刈り戦略（デフォルト: MedianPruner）
        timeout : int, optional
            最適化のタイムアウト時間（秒）
        show_progress : bool, default=True
            進捗表示の有効化
        """
        self.n_trials = n_trials
        self.cv_folds = cv_folds
        self.random_state = random_state
        self.direction = direction
        self.timeout = timeout
        self.show_progress = show_progress
        
        self.is_regression = is_regression
        requested_gpu = str(device_type).lower() == "gpu"
        if requested_gpu:
            detected, reason = detect_lightgbm_device(prefer_gpu=True)
            self.device_type = "gpu" if detected == "gpu" else "cpu"
            logger.info("LightGBM device auto-detect: requested=gpu, selected=%s (%s)", self.device_type, reason)
        else:
            self.device_type = "cpu"
        self.fixed_boosting_type = fixed_boosting_type
        self.n_jobs = n_jobs
        self.gc_after_trial = gc_after_trial
        # 回帰の場合は neg_RMSE を最小化
        if is_regression and direction == "maximize":
            self.direction = "minimize"
        else:
            self.direction = direction

        # サンプラーとプルーナーの設定
        self.sampler = sampler or TPESampler(seed=random_state, multivariate=True)
        self.pruner = pruner or MedianPruner(
            n_startup_trials=10,
            n_warmup_steps=5,
            interval_steps=1
        )
        
        # 最適化結果の保存
        self.best_params: Dict[str, Any] = {}
        self.best_score: float = 0.0
        self.study: Optional[optuna.Study] = None
        self.optimization_history: list = []
    
    def _objective(
        self,
        trial: optuna.Trial,
        X: np.ndarray,
        y: np.ndarray,
        categorical_features: list = None
    ) -> float:
        """
        Optunaの目的関数
        
        Parameters
        ----------
        trial : optuna.Trial
            Optunaのトライアルオブジェクト
        X : np.ndarray
            特徴量
        y : np.ndarray
            ターゲット
        categorical_features : list, optional
            カテゴリカル特徴量のインデックスまたは名前
        
        Returns
        -------
        float
            クロスバリデーションの平均AUC
        """
        # ハイパーパラメータの探索空間を定義
        # is_regression=True の場合は回帰用の objective/metric を使う
        _objective_val = 'regression' if self.is_regression else 'binary'
        _metric_val = 'rmse' if self.is_regression else 'auc'
        _n_estimators = trial.suggest_int('n_estimators', 50, 500)
        _boosting_type = (
            self.fixed_boosting_type
            if self.fixed_boosting_type in ('gbdt', 'dart')
            else trial.suggest_categorical('boosting_type', ['gbdt', 'dart'])
        )
        if self.device_type == 'cpu':
            _boosting_type = 'gbdt'
        params = {
            # 学習制御
            'objective': _objective_val,
            'metric': _metric_val,
            'verbosity': -1,
            'boosting_type': _boosting_type,
            'device_type': self.device_type,
            'random_state': self.random_state,
            
            # ツリー構造
            'num_leaves': trial.suggest_int('num_leaves', 31, 127),
            'max_depth': trial.suggest_int('max_depth', 3, 10),
            'min_child_samples': trial.suggest_int('min_child_samples', 10, 100),
            'min_child_weight': trial.suggest_float('min_child_weight', 1e-4, 1e-1, log=True),
            
            # 学習率
            'learning_rate': trial.suggest_float('learning_rate', 0.001, 0.1, log=True),
            # ※ n_estimators は lgb.train の num_boost_round として別途渡す
            
            # 正則化
            'reg_alpha': trial.suggest_float('reg_alpha', 1e-8, 10.0, log=True),
            'reg_lambda': trial.suggest_float('reg_lambda', 1e-8, 10.0, log=True),
            
            # サンプリング
            'feature_fraction': trial.suggest_float('feature_fraction', 0.6, 1.0),
            'bagging_fraction': trial.suggest_float('bagging_fraction', 0.6, 1.0),
            'bagging_freq': trial.suggest_int('bagging_freq', 0, 7),
            
            # カテゴリカル特徴量の処理
            'max_cat_to_onehot': trial.suggest_int('max_cat_to_onehot', 2, 10),
        }

        if self.device_type == 'gpu':
            params.update({
                'max_bin': 255,
                'gpu_use_dp': False,
                'num_threads': max(1, int(__import__('os').cpu_count() or 1)),
            })
        
        # DART固有のパラメータ
        if params['boosting_type'] == 'dart':
            params['drop_rate'] = trial.suggest_float('drop_rate', 0.0, 0.5)
            params['skip_drop'] = trial.suggest_float('skip_drop', 0.0, 0.5)
        
        # クロスバリデーション（回帰は KFold、分類は StratifiedKFold）
        if self.is_regression:
            cv_splitter = KFold(
                n_splits=self.cv_folds,
                shuffle=True,
                random_state=self.random_state
            )
            split_iter = cv_splitter.split(X)
        else:
            cv_splitter = StratifiedKFold(
                n_splits=self.cv_folds,
                shuffle=True,
                random_state=self.random_state
            )
            split_iter = cv_splitter.split(X, y)

        cv_scores = []

        for fold_idx, (train_idx, valid_idx) in enumerate(split_iter):
            fold_start = time.perf_counter()
            X_train, X_valid = X[train_idx], X[valid_idx]
            y_train, y_valid = y[train_idx], y[valid_idx]
            
            # dtype が object の場合の念のため変換（optimize()で既に変換済みのはず）
            if X_train.dtype != np.float64:
                X_train = X_train.astype(np.float64)
                X_valid = X_valid.astype(np.float64)
            
            # LightGBMデータセット作成
            train_data = lgb.Dataset(
                X_train, y_train,
                categorical_feature=categorical_features
            )
            valid_data = lgb.Dataset(
                X_valid, y_valid,
                categorical_feature=categorical_features,
                reference=train_data
            )
            
            # コールバック設定（Early StoppingとPruning）
            callbacks = [
                lgb.early_stopping(stopping_rounds=50, verbose=False)
            ]
            
            # 学習（n_estimators は num_boost_round として渡す）
            model = lgb.train(
                params,
                train_data,
                num_boost_round=_n_estimators,
                valid_sets=[valid_data],
                callbacks=callbacks
            )
            
            # 検証データで評価（回帰: neg_RMSE を最小化、分類: AUC を最大化）
            y_pred = model.predict(X_valid)
            if self.is_regression:
                score = -np.sqrt(mean_squared_error(y_valid, y_pred))  # neg_RMSE (minimize)
                rmse = abs(float(score))
            else:
                score = roc_auc_score(y_valid, y_pred)
                rmse = float(np.sqrt(mean_squared_error(y_valid, y_pred)))
            cv_scores.append(score)

            # 要件ログ: trial/fold 単位の最適化ログ + GPU利用ログ
            append_optuna_trial_log(
                trial=trial.number,
                fold=fold_idx + 1,
                elapsed=time.perf_counter() - fold_start,
                rmse=rmse,
                score=float(score),
                device=self.device_type,
                params=params,
            )
            append_gpu_usage_log(trial=trial.number)
            
            # Optunaの枝刈り（中間結果を報告）
            trial.report(score, fold_idx)
            if trial.should_prune():
                raise optuna.TrialPruned()
        
        # 平均スコアを返す
        mean_score = np.mean(cv_scores)
        return mean_score
    
    def optimize(
        self,
        X: np.ndarray,
        y: np.ndarray,
        categorical_features: list = None,
        study_name: str = "lightgbm_optimization"
    ) -> Tuple[Dict[str, Any], float]:
        """
        ハイパーパラメータ最適化を実行
        
        Parameters
        ----------
        X : np.ndarray or pd.DataFrame
            特徴量
        y : np.ndarray or pd.Series
            ターゲット
        categorical_features : list, optional
            カテゴリカル特徴量のインデックスまたは名前
        study_name : str, default="lightgbm_optimization"
            スタディ名
        
        Returns
        -------
        best_params : dict
            最適なハイパーパラメータ
        best_score : float
            最良のスコア
        """
        # ── X を numpy float64 に変換 ─────────────────────────────────────────
        # category / object 列（例: '芝', 'ダート'）は列ごとに numpy 配列を構築して
        # DataFrame 代入を経由しない（pandas の dtype 保持挙動に依存しないため）。
        # categorical_features が列名リストの場合もインデックスに変換。
        if isinstance(X, pd.DataFrame):
            _col_names = X.columns.tolist()
            _cat_auto_indices: list = []
            _col_arrays: list = []

            for _i, _col in enumerate(_col_names):
                _s = X[_col]
                if hasattr(_s, 'cat'):               # pandas category dtype
                    _codes = _s.cat.codes.to_numpy(dtype=float)
                    _codes[_codes == -1] = np.nan    # NaN は codes=-1 で表現される
                    _col_arrays.append(_codes)
                    _cat_auto_indices.append(_i)
                elif _s.dtype == object:              # string / mixed object
                    # na_sentinel は pandas 2.0 で削除 → use_na_sentinel=True(デフォルト)を使用
                    _codes_int, _ = pd.factorize(_s)
                    _arr = _codes_int.astype(float)
                    _arr[_arr == -1] = np.nan
                    _col_arrays.append(_arr)
                    _cat_auto_indices.append(_i)
                else:                                # 数値型（int/float/bool/Int64 等）
                    _col_arrays.append(pd.to_numeric(_s, errors='coerce').to_numpy(dtype=float))

            # 列ごとの配列を結合 → float64 matrix（DataFrame 代入を使わないので dtype ズレなし）
            X = np.column_stack(_col_arrays) if _col_arrays else np.empty((len(X), 0), dtype=np.float64)

            # categorical_features: 列名 → インデックスに統一し、自動検出分をマージ
            if categorical_features is not None:
                if len(categorical_features) > 0 and isinstance(categorical_features[0], str):
                    _name_to_idx = {n: i for i, n in enumerate(_col_names)}
                    categorical_features = [
                        _name_to_idx[c] for c in categorical_features if c in _name_to_idx
                    ]
                categorical_features = sorted(set(list(categorical_features) + _cat_auto_indices)) or None
            elif _cat_auto_indices:
                categorical_features = _cat_auto_indices

        elif not isinstance(X, np.ndarray):
            X = np.array(X, dtype=object)
            X[pd.isna(X)] = np.nan
            X = X.astype(np.float64)
        elif X.dtype == object:
            # numpy object array に pd.NA が混入している場合（X.values による変換後など）
            _na_mask_arr = pd.isna(X)
            if _na_mask_arr.any():
                X = X.copy()
                X[_na_mask_arr] = np.nan
            X = X.astype(np.float64)

        if isinstance(y, pd.Series):
            _na_mask_y = y.isna().values
            _y_obj = y.to_numpy(dtype=object)
            _y_obj[_na_mask_y] = np.nan
            y = _y_obj.astype(np.float64)
        elif not isinstance(y, np.ndarray):
            y = np.array(y, dtype=object)
            y[pd.isna(y)] = np.nan
            y = y.astype(np.float64)
        elif y.dtype == object:
            _na_mask_y_arr = pd.isna(y)
            if _na_mask_y_arr.any():
                y = y.copy()
                y[_na_mask_y_arr] = np.nan
            y = y.astype(np.float64)

        # ── y の NaN 行を事前に除去（回帰ターゲットは欠損あり / sklearn は NaN 不可）──
        _valid_y_mask = np.isfinite(y)
        if not _valid_y_mask.all():
            _n_before = len(y)
            X = X[_valid_y_mask]
            y = y[_valid_y_mask]
            print(f"  y の NaN/inf 行を除去: {_n_before - len(y)} 行 → {len(y):,} 行")

        print(f"\n[OPT-001] optimize()メソッド開始")
        print(f"[OPT-002] X.shape={X.shape}, y.shape={y.shape}")
        print(f"[OPT-003] n_trials={self.n_trials}, cv_folds={self.cv_folds}")
        
        print(f"\n{'='*70}")
        print(f"  Optuna ハイパーパラメータ最適化開始")
        print(f"{'='*70}")
        print(f"  試行回数: {self.n_trials}")
        print(f"  CVフォールド: {self.cv_folds}")
        print(f"  データ数: {len(X)}行 × {X.shape[1]}列")
        if categorical_features:
            print(f"  カテゴリカル特徴量: {len(categorical_features)}個")
        if self.timeout:
            print(f"  タイムアウト: {self.timeout}秒")
        print(f"{'='*70}\n")
        
        print(f"[OPT-004] スタディ作成開始")
        # スタディ作成
        self.study = optuna.create_study(
            study_name=study_name,
            direction=self.direction,
            sampler=self.sampler,
            pruner=self.pruner
        )
        print(f"[OPT-005] スタディ作成完了")
        
        print(f"[OPT-006] ★★★ study.optimize()呼び出し直前 ★★★")
        _optimize_started = time.perf_counter()
        # 最適化実行
        self.study.optimize(
            lambda trial: self._objective(trial, X, y, categorical_features),
            n_trials=self.n_trials,
            timeout=self.timeout,
            show_progress_bar=self.show_progress,
            n_jobs=self.n_jobs,
            gc_after_trial=self.gc_after_trial,
        )
        _elapsed = time.perf_counter() - _optimize_started
        print(f"[OPT-007] ★★★ study.optimize()呼び出し完了 ★★★")
        print(f"[OPT-007] ★★★ study.optimize()呼び出し完了 ★★★")
        
        print(f"[OPT-008] 結果保存開始")
        # 結果を保存
        self.best_params = self.study.best_params
        self.best_score = self.study.best_value
        print(f"[OPT-009] best_score={self.best_score:.4f}")
        
        # 最適化履歴を保存
        self.optimization_history = [
            {
                'trial_number': trial.number,
                'value': trial.value,
                'params': trial.params,
                'state': trial.state.name
            }
            for trial in self.study.trials
        ]
        print(f"[OPT-010] 履歴保存完了: {len(self.study.trials)}試行")
        
        # 結果表示
        print(f"\n{'='*70}")
        print(f"  最適化完了")
        print(f"{'='*70}")
        print(f"  最良スコア: {self.best_score:.4f}")
        print(f"  完了試行数: {len([t for t in self.study.trials if t.state == optuna.trial.TrialState.COMPLETE])}")
        print(f"  枝刈り試行数: {len([t for t in self.study.trials if t.state == optuna.trial.TrialState.PRUNED])}")
        print(f"  実行時間: {_elapsed:.1f}秒")
        print(f"  LightGBM Device: {self.device_type}")
        print(f"\n  最適パラメータ:")
        for key, value in self.best_params.items():
            print(f"    {key}: {value}")
        print(f"{'='*70}\n")
        
        return self.best_params, self.best_score
    
    def get_best_model_params(self) -> Dict[str, Any]:
        """
        最適なパラメータをLightGBMの学習用形式で取得
        
        Returns
        -------
        dict
            LightGBM学習用のパラメータ辞書
        """
        if not self.best_params:
            raise ValueError("最適化がまだ実行されていません。optimize()を先に実行してください。")
        
        _objective_val = 'regression' if self.is_regression else 'binary'
        _metric_val = 'rmse' if self.is_regression else 'auc'
        params = {
            'objective': _objective_val,
            'metric': _metric_val,
            'verbosity': -1,
            'random_state': self.random_state,
            **self.best_params
        }
        
        return params
    
    def get_optimization_history(self) -> pd.DataFrame:
        """
        最適化履歴をDataFrameで取得
        
        Returns
        -------
        pd.DataFrame
            最適化履歴
        """
        if not self.optimization_history:
            raise ValueError("最適化がまだ実行されていません。")
        
        return pd.DataFrame(self.optimization_history)
    
    def plot_optimization_history(self, save_path: Optional[str] = None):
        """
        最適化履歴をプロット（要optuna-dashboard or plotly）
        
        Parameters
        ----------
        save_path : str, optional
            保存先のパス
        """
        if not self.study:
            raise ValueError("最適化がまだ実行されていません。")
        
        try:
            import plotly
            from optuna.visualization import (
                plot_optimization_history,
                plot_param_importances,
                plot_parallel_coordinate
            )
            
            # 最適化履歴
            fig1 = plot_optimization_history(self.study)
            if save_path:
                fig1.write_html(save_path.replace('.html', '_history.html'))
            fig1.show()
            
            # パラメータ重要度
            fig2 = plot_param_importances(self.study)
            if save_path:
                fig2.write_html(save_path.replace('.html', '_importance.html'))
            fig2.show()
            
            # パラレルコーディネート
            fig3 = plot_parallel_coordinate(self.study)
            if save_path:
                fig3.write_html(save_path.replace('.html', '_parallel.html'))
            fig3.show()
            
        except ImportError:
            print("プロット機能を使用するにはplotlyとoptuna-dashboardが必要です。")
            print("pip install plotly optuna-dashboard")


def optimize_lightgbm_params(
    X: np.ndarray,
    y: np.ndarray,
    categorical_features: list = None,
    n_trials: int = 100,
    cv_folds: int = 5,
    timeout: Optional[int] = None,
    random_state: int = 42
) -> Tuple[Dict[str, Any], float]:
    """
    LightGBMのハイパーパラメータを最適化する便利関数
    
    Parameters
    ----------
    X : np.ndarray or pd.DataFrame
        特徴量
    y : np.ndarray or pd.Series
        ターゲット
    categorical_features : list, optional
        カテゴリカル特徴量のインデックスまたは名前
    n_trials : int, default=100
        最適化の試行回数
    cv_folds : int, default=5
        クロスバリデーションのフォールド数
    timeout : int, optional
        タイムアウト時間（秒）
    random_state : int, default=42
        乱数シード
    
    Returns
    -------
    best_params : dict
        最適なハイパーパラメータ
    best_score : float
        最良のスコア
    
    Examples
    --------
    >>> from keiba_ai.optuna_optimizer import optimize_lightgbm_params
    >>> X, y = load_data()
    >>> best_params, best_score = optimize_lightgbm_params(X, y, n_trials=50)
    >>> print(f"Best AUC: {best_score:.4f}")
    """
    optimizer = OptunaLightGBMOptimizer(
        n_trials=n_trials,
        cv_folds=cv_folds,
        timeout=timeout,
        random_state=random_state
    )
    
    best_params, best_score = optimizer.optimize(X, y, categorical_features)
    
    return best_params, best_score
