import sys
import os
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from myra_app.ingest_bhavcopy import ingest_bhavcopies
