"""I2S MEMS microphone (Adafruit SPH0645LM4H-LB breakout #3421).

Hardware: Adafruit I2S MEMS Microphone Breakout #3421
          https://www.adafruit.com/product/3421
Interface: I2S (bit clock, word select, data)
Output: 24-bit PDM / I2S (18-bit precision), left-justified

Wiring (ESP32 Feather V2):
    BCLK → board.D27  (GPIO27)
    LRCL → board.D13  (GPIO13 — labeled "led" on the pinout PDF)
    DOUT → board.A2   (GPIO34, input-only)
    SEL  → GND on the breakout PCB (selects left-channel output)

Note: requires audiobusio.I2SIn support in the CircuitPython build.
"""
