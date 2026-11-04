from orbitcu.ingest.s5p import processing_time


def test_processing_time_from_filename():
    href = ("https://x.blob.core.windows.net/sentinel-5p/TROPOMI/L2__SO2___/2026/06/03/"
            "S5P_NRTI_L2__SO2____20260603T125220_20260603T125720_44763_03_020800_20260603T133139.nc?sig=abc")
    assert processing_time(href).isoformat() == "2026-06-03T13:31:39+00:00"
