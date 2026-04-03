#!/usr/bin/env python
from .rs_factor import RSFactor
from .delivery_factor import DeliveryFactor
from .ias_factor import IASFactor

GLOBAL_FACTORS = {
    "rs": RSFactor(weight=0.3),
    "delivery": DeliveryFactor(weight=0.3),
    "ias": IASFactor(weight=0.4)
}
