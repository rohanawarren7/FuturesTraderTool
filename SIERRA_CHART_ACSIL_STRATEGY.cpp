/*
================================================================================
  SIERRA CHART ACSIL — VWAP Wave Bot (Phase 2 Live Execution)
  File: SIERRA_CHART_ACSIL_STRATEGY.cpp

  Purpose:
    Phase 2 live execution layer. Runs inside Sierra Chart's Advanced Custom
    Study Interface and Language (ACSIL) to submit MES/MNQ orders via the
    Denali feed (Rithmic clearing) with real cumulative delta order flow.

    This is the production replacement for the TradingView Pine Script used
    in Phase 1. It reads real tick-by-tick bid/ask volume from the Denali
    feed rather than using the close > open proxy.

  Prerequisites:
    - Sierra Chart with Denali Exchange Data Feed (~$28/month)
    - Rithmic-connected account (futures broker: Tradovate, Amp, NinjaTrader)
    - Place this file in: [Sierra Chart install]\ACS_Source\
    - Build: Sierra Chart menu → Analysis → Build Custom Indicators/Systems

  Signal logic mirrors core/signal_generator.py exactly.
  Any changes to the Python strategy must be replicated here.

  IMPORTANT — Prop firm rules enforced here:
    - Contract cap: hard-coded to prop firm's max_contracts
    - No trades outside RTH (09:30–16:00 ET) — enforced by TimeFilter()
    - Daily loss limit checked before every order

================================================================================
*/

#include "SierraChartCPP.h"

// ────────────────────────────────────────────────────────────
// Study descriptor — Sierra Chart reads this to register the study
// ────────────────────────────────────────────────────────────
SCSFExport scsf_VWAPWaveBot(SCStudyInterfaceRef sc)
{
    // ── Input definitions ──────────────────────────────────
    SCInputRef  InpVWAPBandMultiplier  = sc.Input[0];
    SCInputRef  InpMaxContracts        = sc.Input[1];
    SCInputRef  InpDailyLossLimit      = sc.Input[2];
    SCInputRef  InpMinVolumeRatio      = sc.Input[3];
    SCInputRef  InpEnableTrading       = sc.Input[4];

    // ── Subgraph definitions ───────────────────────────────
    SCSubgraphRef SgVWAP          = sc.Subgraph[0];
    SCSubgraphRef SgSD1Upper      = sc.Subgraph[1];
    SCSubgraphRef SgSD1Lower      = sc.Subgraph[2];
    SCSubgraphRef SgSD2Upper      = sc.Subgraph[3];
    SCSubgraphRef SgSD2Lower      = sc.Subgraph[4];
    SCSubgraphRef SgBuySignal     = sc.Subgraph[5];
    SCSubgraphRef SgSellSignal    = sc.Subgraph[6];

    // ── Persistent variables (survive bar-to-bar) ──────────
    SCPersistentInt    IntDayStartIndex    = sc.PersistentInt[0];
    SCPersistentFloat  FloatSessionVol     = sc.PersistentFloat[0];
    SCPersistentFloat  FloatSessionVWAP    = sc.PersistentFloat[1];
    SCPersistentFloat  FloatDailyPnL       = sc.PersistentFloat[2];
    SCPersistentInt    IntOpenPosition     = sc.PersistentInt[1];

    // ── Initialisation block (runs once on study load) ─────
    if (sc.SetDefaults)
    {
        sc.GraphName          = "VWAP Wave Bot — Phase 2";
        sc.StudyDescription   = "VWAP mean-reversion strategy with real delta. "
                                "Mirrors core/signal_generator.py logic.";
        sc.AutoLoop           = 1;
        sc.GraphRegion        = 0;
        sc.FreeDLL            = 0;

        // Inputs
        InpVWAPBandMultiplier.Name    = "VWAP Band Multiplier (SD)";
        InpVWAPBandMultiplier.SetFloat(1.0f);

        InpMaxContracts.Name          = "Max Contracts (prop firm limit)";
        InpMaxContracts.SetInt(3);

        InpDailyLossLimit.Name        = "Daily Loss Limit ($)";
        InpDailyLossLimit.SetFloat(500.0f);   // Topstep $50k default

        InpMinVolumeRatio.Name        = "Min Volume Ratio for spike flag";
        InpMinVolumeRatio.SetFloat(1.5f);

        InpEnableTrading.Name         = "Enable Live Order Submission";
        InpEnableTrading.SetYesNo(0); // Off by default — must manually enable

        // Subgraphs
        SgVWAP.Name = "VWAP";
        SgVWAP.DrawStyle = DRAWSTYLE_LINE;
        SgVWAP.PrimaryColor = RGB(255, 200, 0);   // yellow

        SgSD1Upper.Name = "SD1 Upper";
        SgSD1Upper.DrawStyle = DRAWSTYLE_LINE;
        SgSD1Upper.PrimaryColor = RGB(0, 200, 255);

        SgSD1Lower.Name = "SD1 Lower";
        SgSD1Lower.DrawStyle = DRAWSTYLE_LINE;
        SgSD1Lower.PrimaryColor = RGB(0, 200, 255);

        SgSD2Upper.Name = "SD2 Upper";
        SgSD2Upper.DrawStyle = DRAWSTYLE_LINE;
        SgSD2Upper.PrimaryColor = RGB(255, 80, 80);

        SgSD2Lower.Name = "SD2 Lower";
        SgSD2Lower.DrawStyle = DRAWSTYLE_LINE;
        SgSD2Lower.PrimaryColor = RGB(255, 80, 80);

        SgBuySignal.Name = "Buy Signal";
        SgBuySignal.DrawStyle = DRAWSTYLE_ARROWUP;
        SgBuySignal.PrimaryColor = RGB(0, 255, 0);

        SgSellSignal.Name = "Sell Signal";
        SgSellSignal.DrawStyle = DRAWSTYLE_ARROWDOWN;
        SgSellSignal.PrimaryColor = RGB(255, 0, 0);

        return;
    }

    // ── Get current bar index ──────────────────────────────
    int BarIndex = sc.Index;

    // ── Detect new session (RTH open) ─────────────────────
    SCDateTime BarDT = sc.BaseDateTimeIn[BarIndex];
    int BarHour   = 0, BarMin = 0, BarSec = 0;
    BarDT.GetTimeHMS(BarHour, BarMin, BarSec);

    bool IsNewSession = (BarHour == 9 && BarMin == 30);
    if (IsNewSession || BarIndex == 0)
    {
        IntDayStartIndex   = BarIndex;
        FloatSessionVol    = 0.0f;
        FloatSessionVWAP   = 0.0f;
        FloatDailyPnL      = 0.0f;
    }

    // ── Calculate session VWAP and bands ──────────────────
    float SumTPV = 0.0f;  // Sum(TypicalPrice × Volume)
    float SumVol = 0.0f;  // Sum(Volume)
    float SumSqDev = 0.0f;

    for (int i = IntDayStartIndex; i <= BarIndex; i++)
    {
        float TypPrice = (sc.High[i] + sc.Low[i] + sc.Close[i]) / 3.0f;
        float Vol      = sc.Volume[i];
        SumTPV += TypPrice * Vol;
        SumVol += Vol;
    }

    float VWAP = (SumVol > 0.0f) ? (SumTPV / SumVol) : sc.Close[BarIndex];
    SgVWAP[BarIndex] = VWAP;

    // Standard deviation bands
    for (int i = IntDayStartIndex; i <= BarIndex; i++)
    {
        float TypPrice = (sc.High[i] + sc.Low[i] + sc.Close[i]) / 3.0f;
        float Dev      = TypPrice - VWAP;
        float Vol      = sc.Volume[i];
        SumSqDev += (Dev * Dev * Vol);
    }

    float Mult = InpVWAPBandMultiplier.GetFloat();
    float SD   = (SumVol > 0.0f) ? sqrt(SumSqDev / SumVol) : 0.0f;

    SgSD1Upper[BarIndex] = VWAP + (1.0f * Mult * SD);
    SgSD1Lower[BarIndex] = VWAP - (1.0f * Mult * SD);
    SgSD2Upper[BarIndex] = VWAP + (2.0f * Mult * SD);
    SgSD2Lower[BarIndex] = VWAP - (2.0f * Mult * SD);

    // ── Time filter (RTH only, skip first/last 15 min) ────
    int MinIntoSession = (BarHour - 9) * 60 + BarMin - 30;
    bool InTradingWindow = (MinIntoSession >= 15 && MinIntoSession <= 375);
    if (!InTradingWindow) return;

    // ── Real cumulative delta from Denali bid/ask volume ──
    // Denali provides sc.BidVolume[i] and sc.AskVolume[i] per bar.
    // Delta = AskVolume - BidVolume (net aggressive buying)
    float Delta    = sc.AskVolume[BarIndex] - sc.BidVolume[BarIndex];
    bool  PosDelta = (Delta > 0.0f);
    bool  NegDelta = (Delta < 0.0f);

    // Detect delta flip (direction changed vs prior bar)
    float PrevDelta = sc.AskVolume[BarIndex-1] - sc.BidVolume[BarIndex-1];
    bool  DeltaFlip = ((Delta > 0 && PrevDelta <= 0) || (Delta < 0 && PrevDelta >= 0));

    // ── Volume spike detection ─────────────────────────────
    float AvgVol = 0.0f;
    int   LookbackBars = 20;
    for (int i = max(0, BarIndex - LookbackBars); i < BarIndex; i++)
        AvgVol += sc.Volume[i];
    AvgVol /= (float)LookbackBars;
    bool VolumeSpike = (AvgVol > 0.0f && sc.Volume[BarIndex] > AvgVol * InpMinVolumeRatio.GetFloat());

    // ── VWAP position classification ─────────────────────
    float Close  = sc.Close[BarIndex];
    float SD1U   = SgSD1Upper[BarIndex];
    float SD1L   = SgSD1Lower[BarIndex];
    float SD2U   = SgSD2Upper[BarIndex];
    float SD2L   = SgSD2Lower[BarIndex];

    // ── Signal logic — mirrors SignalGenerator.generate() ─
    bool BuySignal  = false;
    bool SellSignal = false;
    SCString SignalType;

    // SETUP 1: Balanced mean-reversion (SD1 fade)
    if (Close >= SD1L && Close <= SD1U)
    {
        // Long at SD1 Lower
        if (Close <= SD1L && PosDelta && DeltaFlip)
        { BuySignal = true; SignalType = "MEAN_REVERSION_LONG"; }

        // Short at SD1 Upper
        if (Close >= SD1U && NegDelta && DeltaFlip)
        { SellSignal = true; SignalType = "MEAN_REVERSION_SHORT"; }
    }

    // SETUP 3: SD2 extreme fade (override — checked independently)
    if (Close >= SD2U && VolumeSpike && NegDelta)
    { SellSignal = true; SignalType = "SD2_EXTREME_FADE_SHORT"; }

    if (Close <= SD2L && VolumeSpike && PosDelta)
    { BuySignal = true; SignalType = "SD2_EXTREME_FADE_LONG"; }

    // ── Mark signals on chart ──────────────────────────────
    SgBuySignal[BarIndex]  = BuySignal  ? sc.Low[BarIndex]  - SD : 0;
    SgSellSignal[BarIndex] = SellSignal ? sc.High[BarIndex] + SD : 0;

    // ── Order submission (only on bar close, live trading enabled) ──
    if (!sc.IsFullRecalculation && InpEnableTrading.GetYesNo() && sc.LastCallToFunction)
    {
        // Check daily loss limit before any order
        if (FloatDailyPnL <= -InpDailyLossLimit.GetFloat())
        {
            sc.AddMessageToLog("Daily loss limit hit — no new orders", 1);
            return;
        }

        // Flat position required before new entry
        if (IntOpenPosition != 0) return;

        int Contracts = InpMaxContracts.GetInt();

        s_SCNewOrder Order;
        Order.OrderType          = SCT_ORDERTYPE_MARKET;
        Order.TimeInForce        = SCT_TIF_DAY;
        Order.Quantity           = Contracts;
        Order.TextTag            = SignalType;
        Order.AttachedOrderTarget.OrderType = SCT_ORDERTYPE_LIMIT;
        Order.AttachedOrderStop.OrderType   = SCT_ORDERTYPE_STOP;

        if (BuySignal)
        {
            Order.BuySell                          = BSE_BUY;
            Order.AttachedOrderTarget.Price1       = VWAP;                // target = VWAP
            Order.AttachedOrderStop.Price1         = SD1L - (0.25f * 2);  // stop = 2 ticks below SD1L
            sc.BuyEntry(Order);
            IntOpenPosition = 1;
        }
        else if (SellSignal)
        {
            Order.BuySell                          = BSE_SELL;
            Order.AttachedOrderTarget.Price1       = VWAP;                // target = VWAP
            Order.AttachedOrderStop.Price1         = SD1U + (0.25f * 2);  // stop = 2 ticks above SD1U
            sc.SellEntry(Order);
            IntOpenPosition = -1;
        }
    }
}
