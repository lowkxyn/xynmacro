"""Finite and bounded value coercion helpers."""

import math


def _finite_float(value, minimum=0.0):
    """Coerce to float, rejecting NaN/inf and clamping below `minimum`. A
    non-finite or negative timing/threshold value must never reach the macro
    loops or get persisted — it stalls or destabilises detection."""
    f = float(value)
    if not math.isfinite(f):
        raise ValueError("value must be a finite number")
    return max(minimum, f)


def _finite_int(value, minimum=0):
    f = float(value)
    if not math.isfinite(f):
        raise ValueError("value must be a finite number")
    return max(minimum, int(f))


def _bounded_float(value, minimum, maximum):
    import xynmacro_core as core

    return min(float(maximum), core._finite_float(value, float(minimum)))


def _bounded_int(value, minimum, maximum):
    import xynmacro_core as core

    return min(int(maximum), core._finite_int(value, int(minimum)))
