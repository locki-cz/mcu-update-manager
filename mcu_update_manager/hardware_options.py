"""Hardware choices supported by the automatic Klipper config generator."""

PROCESSORS = {
    "STM32F072": {"chip": "stm32f072xb", "chips": ["stm32f072xb", "stm32f072xx"], "offsets": ["No bootloader", "8KiB", "16KiB"], "can": ["PA11/PA12", "PA11/PB9", "PB8/PB9", "PD0/PD1"]},
    "STM32F103": {"chip": "stm32f103xe", "chips": ["stm32f103xe", "stm32f103xx"], "offsets": ["No bootloader", "8KiB", "16KiB", "32KiB", "64KiB"], "can": ["PA11/PA12", "PA11/PB9", "PB8/PB9", "PD0/PD1"]},
    "STM32F405": {"chip": "stm32f405xx", "offsets": ["No bootloader", "16KiB", "32KiB", "64KiB"], "can": ["PB8/PB9", "PI9/PH13", "PB5/PB6", "PB12/PB13", "PD0/PD1"]},
    "STM32F407": {"chip": "stm32f407xx", "offsets": ["No bootloader", "16KiB", "32KiB", "64KiB"], "can": ["PB8/PB9", "PI9/PH13", "PB5/PB6", "PB12/PB13", "PD0/PD1"]},
    "STM32F429": {"chip": "stm32f429xx", "offsets": ["No bootloader", "16KiB", "32KiB", "64KiB"], "can": ["PB8/PB9", "PI9/PH13", "PB5/PB6", "PB12/PB13", "PD0/PD1"]},
    "STM32F446": {"chip": "stm32f446xx", "offsets": ["No bootloader", "32KiB", "64KiB"], "can": ["PB8/PB9", "PI9/PH13", "PB5/PB6", "PB12/PB13", "PD0/PD1"]},
    "STM32G0B1": {"chip": "stm32g0b1xx", "offsets": ["No bootloader", "8KiB"], "can": ["PB8/PB9", "PB12/PB13", "PD0/PD1", "PB0/PB1", "PD12/PD13", "PC2/PC3"]},
    "STM32H723": {"chip": "stm32h723xx", "offsets": ["No bootloader", "128KiB"], "can": ["PB8/PB9", "PB5/PB6", "PB12/PB13", "PD0/PD1", "PB0/PB1", "PD12/PD13", "PC2/PC3"]},
    "STM32H743": {"chip": "stm32h743xx", "offsets": ["No bootloader", "128KiB"], "can": ["PB8/PB9", "PB5/PB6", "PB12/PB13", "PD0/PD1", "PB0/PB1", "PD12/PD13", "PC2/PC3", "PH13/PH14"]},
    "RP2040": {"chip": "rp2040", "offsets": ["No bootloader", "16KiB"], "can": []},
}

CLOCKS = ["8MHz crystal", "12MHz crystal", "25MHz crystal"]
KATAPULT_OFFSETS = {
    "STM32F072": ["8KiB"], "STM32F103": ["8KiB"],
    "STM32F405": ["16KiB", "32KiB"], "STM32F407": ["16KiB", "32KiB"],
    "STM32F429": ["16KiB", "32KiB"], "STM32F446": ["16KiB", "32KiB"],
    "STM32G0B1": ["8KiB"], "STM32H723": ["128KiB"],
    "STM32H743": ["128KiB"], "RP2040": ["16KiB"],
}
RP2040_CAN_PAIRS = ["gpio1/gpio0", "gpio4/gpio5"]


def processor_options() -> dict:
    result = {}
    for processor, value in PROCESSORS.items():
        rp2040 = processor == "RP2040"
        direct_can = list(RP2040_CAN_PAIRS) if rp2040 else list(dict.fromkeys(["PA11/PA12", "PA11/PB9", *value["can"]]))
        bridge_can = list(RP2040_CAN_PAIRS) if rp2040 else [pair for pair in value["can"] if pair not in {"PA11/PA12", "PA11/PB9"}]
        katapult_can = list(direct_can)
        if processor == "STM32G0B1":
            bridge_can.insert(1, "PB5/PB6")
            katapult_can.insert(2, "PB5/PB6")
        if processor in {"STM32H723", "STM32H743"}:
            katapult_can.remove("PB5/PB6")
        result[processor] = {
            **value,
            "architecture": "rp2040" if rp2040 else "stm32",
            "chips": value.get("chips", [value["chip"]]),
            "offsets": list(value["offsets"]),
            "katapult_offsets": list(KATAPULT_OFFSETS[processor]),
            "clock_references": [] if rp2040 else list(CLOCKS),
            "communications": ["usb", "canbus"] if processor == "STM32F103" else ["usb", "canbus", "usb_to_canbus_bridge"],
            "can": direct_can,
            "katapult_can": katapult_can,
            "bridge_can": bridge_can,
        }
    return result
