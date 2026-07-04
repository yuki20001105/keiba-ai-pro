"""Unified training package facade."""

from .config import TrainingPipelineConfig
from .pipeline import run_notebook_pipeline

__all__ = ["TrainingPipelineConfig", "run_notebook_pipeline"]
