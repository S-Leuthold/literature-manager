"""py2app configuration for building Literature Manager.app v2.0"""

from setuptools import setup

APP = ["src/literature_manager/app/menubar.py"]

# Include icon files
DATA_FILES = [
    ("icons", [
        "src/literature_manager/app/icons/icon.png",
        "src/literature_manager/app/icons/icon@2x.png",
    ]),
]

OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "Literature Manager",
        "CFBundleDisplayName": "Literature Manager",
        "CFBundleIdentifier": "com.samleuthold.literature-manager",
        "CFBundleVersion": "2.0.0",
        "CFBundleShortVersionString": "2.0.0",
        "LSUIElement": True,  # Menu bar only, no dock icon
        "NSHighResolutionCapable": True,
    },
    # Minimal packages - we use subprocess for heavy lifting
    "packages": ["literature_manager.app"],
    "includes": [
        "rumps",
        "yaml",
        "dotenv",
    ],
    # Exclude heavy dependencies (not needed for menu bar shell)
    "excludes": [
        "anthropic",
        "pyzotero",
        "sklearn",
        "PyPDF2",
        "pdfplumber",
        "pypdfium2",
        "pypdfium2_raw",
        "watchdog",
    ],
}

setup(
    name="Literature Manager",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
