# Project overview

## Goal

Copper supply is usually known only weeks after the fact, through monthly
statistics. Smelters, however, leave visible traces from space: hot furnaces
in short-wave and thermal infrared, and sulphur dioxide (SO2) plumes. OrbitCu
watches the 30 largest primary copper smelters with free satellite data and
answers one question per site and per day: *is it running?*

From that daily answer the pipeline builds capacity-weighted activity indices
(global and regional), uses them to nowcast monthly refined copper output, and
checks honestly whether the signal improves copper futures strategies.

## Client and needs

The intended client is a commodity trading or research desk.

| Need | What it implies |
|---|---|
| Timeliness | An estimate days or weeks before official monthly statistics |
| Transparency | Every number can be traced back to a raw observation |
| Point-in-time history | Each record stores when it became available, so backtests have no look-ahead bias |
| Actionability | Production nowcasts and a rigorous evaluation on futures |
| Low cost | Free data and open-source software (cloud usage covered by Azure for Students) |

The reference competitor is Earth-i SAVANT, a closed, paid service. No open,
end-to-end system currently produces a smelter activity index and evaluates it
for trading.

**Out of scope:** real-money trading, commercial high-resolution imagery, mine
monitoring.

## Requirements

1. **Smelter registry**: a versioned catalogue (id, coordinates, areas of
   interest, capacity, operator) used as the single reference by every stage.
2. **Satellite ingestion**: Sentinel-2, Landsat 8/9, VIIRS, Sentinel-5P SO2
   and ERA5 winds; history from 2018, then incremental updates.
3. **Site activity detection**: per site and acquisition, a SWIR thermal
   hotspot score and an SO2 emission estimate, each with a cloud / quality flag.
4. **Fusion and indices**: a daily probability that each site is active,
   robust to cloud gaps; capacity-weighted global and regional indices.
5. **Validation**: comparison with a catalogue of known events (shutdowns,
   restarts), official production statistics and public SAVANT figures.
6. **Market data and nowcast**: futures prices, exchange inventories and
   official statistics *with their publication dates*; monthly production nowcast.
7. **Backtest**: walk-forward evaluation, contract rolls, transaction costs,
   performance and risk metrics.
8. **Dashboard**: map of smelters and their status, per-site time series,
   indices, nowcasts, backtest results.
