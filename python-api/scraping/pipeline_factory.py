from __future__ import annotations

from scraping.pipelines.horse_pipeline import HorsePipeline
from scraping.pipelines.odds_pipeline import OddsPipeline
from scraping.pipelines.pedigree_pipeline import PedigreePipeline
from scraping.pipelines.race_pipeline import RacePipeline
from scraping.pipelines.result_pipeline import ResultPipeline


class PipelineFactory:
    def __init__(self):
        self._pipelines = {
            "race": RacePipeline(),
            "horse": HorsePipeline(),
            "odds": OddsPipeline(),
            "result": ResultPipeline(),
            "pedigree": PedigreePipeline(),
        }

    def get(self, name: str):
        key = str(name).strip().lower()
        if key not in self._pipelines:
            raise ValueError(f"unsupported pipeline: {name}")
        return self._pipelines[key]
