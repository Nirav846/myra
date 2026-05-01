#!/usr/bin/env python
from abc import ABC, abstractmethod

import pandas as pd


class BaseFactor(ABC):
    """
    Base class for all MYRA Factors.
    Each factor returns a normalized score from 0.0 to 1.0.
    """

    def __init__(self, weight=1.0):
        self.weight = weight

    @abstractmethod
    def calculate(self, df: pd.DataFrame, funda: dict) -> float:
        """
        Returns a score between 0.0 and 1.0.
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass
