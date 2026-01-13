"""
全モデルのOptunaハイパーパラメータ最適化
LightGBM以外のモデル（LogisticRegression, RandomForest, GradientBoosting）にも対応
"""
import optuna
from optuna.samplers import TPESampler
from optuna.pruners import MedianPruner
import numpy as np
from sklearn.model_selection import StratifiedKFold
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from typing import Dict, Any, Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class OptunaLogisticRegressionOptimizer:
    """ロジスティック回帰のハイパーパラメータ最適化"""
    
    def __init__(self, n_trials: int = 100, cv_folds: int = 5, timeout: int = 300):
        """
        Args:
            n_trials: 試行回数
            cv_folds: クロスバリデーションの分割数
            timeout: タイムアウト（秒）
        """
        self.n_trials = n_trials
        self.cv_folds = cv_folds
        self.timeout = timeout
        self.best_params: Optional[Dict[str, Any]] = None
        self.best_score: float = 0.0
        
    def _objective(self, trial: optuna.Trial, X, y) -> float:
        """
        Optuna目的関数
        
        Args:
            trial: Optunaトライアル
            X: 特徴量
            y: ターゲット
        
        Returns:
            CVスコア（AUC）
        """
        # ハイパーパラメータ探索空間
        params = {
            'C': trial.suggest_float('C', 1e-4, 100.0, log=True),
            'penalty': trial.suggest_categorical('penalty', ['l1', 'l2', 'elasticnet']),
            'solver': trial.suggest_categorical('solver', ['liblinear', 'saga']),
            'max_iter': trial.suggest_int('max_iter', 100, 1000),
            'class_weight': trial.suggest_categorical('class_weight', ['balanced', None]),
        }
        
        # penaltyとsolverの互換性チェック
        if params['penalty'] == 'l1' and params['solver'] not in ['liblinear', 'saga']:
            params['solver'] = 'liblinear'
        elif params['penalty'] == 'elasticnet':
            params['solver'] = 'saga'
            params['l1_ratio'] = trial.suggest_float('l1_ratio', 0.0, 1.0)
        
        # クロスバリデーション
        cv = StratifiedKFold(n_splits=self.cv_folds, shuffle=True, random_state=42)
        cv_scores = []
        
        for fold, (train_idx, val_idx) in enumerate(cv.split(X, y)):
            X_train, X_val = X[train_idx], X[val_idx]
            y_train, y_val = y[train_idx], y[val_idx]
            
            model = LogisticRegression(**params, random_state=42)
            model.fit(X_train, y_train)
            
            y_pred_proba = model.predict_proba(X_val)[:, 1]
            auc = roc_auc_score(y_val, y_pred_proba)
            cv_scores.append(auc)
            
            # 中間レポート（枝刈り用）
            trial.report(auc, fold)
            
            if trial.should_prune():
                raise optuna.TrialPruned()
        
        return np.mean(cv_scores)
    
    def optimize(self, X, y) -> Dict[str, Any]:
        """
        最適化実行
        
        Args:
            X: 特徴量（numpy配列）
            y: ターゲット（numpy配列）
        
        Returns:
            最適パラメータ
        """
        logger.info(f"🔍 ロジスティック回帰の最適化開始: {self.n_trials}試行")
        
        study = optuna.create_study(
            direction='maximize',
            sampler=TPESampler(seed=42),
            pruner=MedianPruner(n_startup_trials=10, n_warmup_steps=3)
        )
        
        study.optimize(
            lambda trial: self._objective(trial, X, y),
            n_trials=self.n_trials,
            timeout=self.timeout,
            show_progress_bar=True
        )
        
        self.best_params = study.best_params
        self.best_score = study.best_value
        
        logger.info(f"✓ 最適化完了:")
        logger.info(f"  - ベストAUC: {self.best_score:.4f}")
        logger.info(f"  - ベストパラメータ: {self.best_params}")
        
        return self.best_params


class OptunaRandomForestOptimizer:
    """ランダムフォレストのハイパーパラメータ最適化"""
    
    def __init__(self, n_trials: int = 100, cv_folds: int = 5, timeout: int = 300):
        self.n_trials = n_trials
        self.cv_folds = cv_folds
        self.timeout = timeout
        self.best_params: Optional[Dict[str, Any]] = None
        self.best_score: float = 0.0
        
    def _objective(self, trial: optuna.Trial, X, y) -> float:
        """Optuna目的関数"""
        # ハイパーパラメータ探索空間
        params = {
            'n_estimators': trial.suggest_int('n_estimators', 50, 500),
            'max_depth': trial.suggest_int('max_depth', 3, 20),
            'min_samples_split': trial.suggest_int('min_samples_split', 2, 20),
            'min_samples_leaf': trial.suggest_int('min_samples_leaf', 1, 10),
            'max_features': trial.suggest_categorical('max_features', ['sqrt', 'log2', None]),
            'bootstrap': trial.suggest_categorical('bootstrap', [True, False]),
            'class_weight': trial.suggest_categorical('class_weight', ['balanced', 'balanced_subsample', None]),
        }
        
        # クロスバリデーション
        cv = StratifiedKFold(n_splits=self.cv_folds, shuffle=True, random_state=42)
        cv_scores = []
        
        for fold, (train_idx, val_idx) in enumerate(cv.split(X, y)):
            X_train, X_val = X[train_idx], X[val_idx]
            y_train, y_val = y[train_idx], y[val_idx]
            
            model = RandomForestClassifier(**params, random_state=42, n_jobs=-1)
            model.fit(X_train, y_train)
            
            y_pred_proba = model.predict_proba(X_val)[:, 1]
            auc = roc_auc_score(y_val, y_pred_proba)
            cv_scores.append(auc)
            
            trial.report(auc, fold)
            
            if trial.should_prune():
                raise optuna.TrialPruned()
        
        return np.mean(cv_scores)
    
    def optimize(self, X, y) -> Dict[str, Any]:
        """最適化実行"""
        logger.info(f"🌲 ランダムフォレストの最適化開始: {self.n_trials}試行")
        
        study = optuna.create_study(
            direction='maximize',
            sampler=TPESampler(seed=42),
            pruner=MedianPruner(n_startup_trials=10, n_warmup_steps=3)
        )
        
        study.optimize(
            lambda trial: self._objective(trial, X, y),
            n_trials=self.n_trials,
            timeout=self.timeout,
            show_progress_bar=True
        )
        
        self.best_params = study.best_params
        self.best_score = study.best_value
        
        logger.info(f"✓ 最適化完了:")
        logger.info(f"  - ベストAUC: {self.best_score:.4f}")
        logger.info(f"  - ベストパラメータ: {self.best_params}")
        
        return self.best_params


class OptunaGradientBoostingOptimizer:
    """勾配ブースティングのハイパーパラメータ最適化"""
    
    def __init__(self, n_trials: int = 100, cv_folds: int = 5, timeout: int = 300):
        self.n_trials = n_trials
        self.cv_folds = cv_folds
        self.timeout = timeout
        self.best_params: Optional[Dict[str, Any]] = None
        self.best_score: float = 0.0
        
    def _objective(self, trial: optuna.Trial, X, y) -> float:
        """Optuna目的関数"""
        # ハイパーパラメータ探索空間
        params = {
            'learning_rate': trial.suggest_float('learning_rate', 0.001, 0.3, log=True),
            'n_estimators': trial.suggest_int('n_estimators', 50, 500),
            'max_depth': trial.suggest_int('max_depth', 3, 10),
            'min_samples_split': trial.suggest_int('min_samples_split', 2, 20),
            'min_samples_leaf': trial.suggest_int('min_samples_leaf', 1, 10),
            'subsample': trial.suggest_float('subsample', 0.5, 1.0),
            'max_features': trial.suggest_categorical('max_features', ['sqrt', 'log2', None]),
        }
        
        # クロスバリデーション
        cv = StratifiedKFold(n_splits=self.cv_folds, shuffle=True, random_state=42)
        cv_scores = []
        
        for fold, (train_idx, val_idx) in enumerate(cv.split(X, y)):
            X_train, X_val = X[train_idx], X[val_idx]
            y_train, y_val = y[train_idx], y[val_idx]
            
            model = GradientBoostingClassifier(**params, random_state=42)
            model.fit(X_train, y_train)
            
            y_pred_proba = model.predict_proba(X_val)[:, 1]
            auc = roc_auc_score(y_val, y_pred_proba)
            cv_scores.append(auc)
            
            trial.report(auc, fold)
            
            if trial.should_prune():
                raise optuna.TrialPruned()
        
        return np.mean(cv_scores)
    
    def optimize(self, X, y) -> Dict[str, Any]:
        """最適化実行"""
        logger.info(f"📈 勾配ブースティングの最適化開始: {self.n_trials}試行")
        
        study = optuna.create_study(
            direction='maximize',
            sampler=TPESampler(seed=42),
            pruner=MedianPruner(n_startup_trials=10, n_warmup_steps=3)
        )
        
        study.optimize(
            lambda trial: self._objective(trial, X, y),
            n_trials=self.n_trials,
            timeout=self.timeout,
            show_progress_bar=True
        )
        
        self.best_params = study.best_params
        self.best_score = study.best_value
        
        logger.info(f"✓ 最適化完了:")
        logger.info(f"  - ベストAUC: {self.best_score:.4f}")
        logger.info(f"  - ベストパラメータ: {self.best_params}")
        
        return self.best_params


def optimize_model(model_type: str, X, y, n_trials: int = 100, timeout: int = 300) -> Dict[str, Any]:
    """
    モデルタイプに応じて最適化を実行
    
    Args:
        model_type: 'logistic', 'random_forest', 'gradient_boosting'
        X: 特徴量
        y: ターゲット
        n_trials: 試行回数
        timeout: タイムアウト（秒）
    
    Returns:
        最適パラメータ
    """
    if model_type == 'logistic':
        optimizer = OptunaLogisticRegressionOptimizer(n_trials=n_trials, timeout=timeout)
    elif model_type == 'random_forest':
        optimizer = OptunaRandomForestOptimizer(n_trials=n_trials, timeout=timeout)
    elif model_type == 'gradient_boosting':
        optimizer = OptunaGradientBoostingOptimizer(n_trials=n_trials, timeout=timeout)
    else:
        raise ValueError(f"未対応のモデルタイプ: {model_type}")
    
    return optimizer.optimize(X, y)


if __name__ == "__main__":
    # テストコード
    from sklearn.datasets import make_classification
    
    print("\n" + "="*80)
    print("全モデル最適化テスト")
    print("="*80)
    
    # テストデータ生成
    X, y = make_classification(n_samples=1000, n_features=20, random_state=42)
    
    # 各モデルの最適化テスト
    for model_type in ['logistic', 'random_forest', 'gradient_boosting']:
        print(f"\n{'='*80}")
        print(f"モデル: {model_type}")
        print(f"{'='*80}")
        
        best_params = optimize_model(model_type, X, y, n_trials=20, timeout=60)
        
        print(f"\n✓ {model_type}の最適化完了")
        print(f"  パラメータ: {best_params}")
