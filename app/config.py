from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_ADB = Path.home() / ".local/opt/platform-tools/adb"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    middleman_api_key: str = Field(..., alias="MIDDLEMAN_API_KEY")
    appium_url: str = Field(default="http://127.0.0.1:4723", alias="APPIUM_URL")
    appium_bin: str = Field(default="", alias="APPIUM_BIN")
    appium_spawn: bool = Field(default=True, alias="APPIUM_SPAWN")
    adb_bin: str = Field(default="", alias="ADB_BIN")
    adb_serial: str = Field(default="", alias="ADB_SERIAL")
    letterboxd_package: str = Field(
        default="com.letterboxd.letterboxd",
        alias="LETTERBOXD_PACKAGE",
    )
    letterboxd_activity: str = Field(
        default="com.letterboxd.letterboxd.MainActivity",
        alias="LETTERBOXD_ACTIVITY",
    )
    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8787, alias="PORT")

    @field_validator("middleman_api_key")
    @classmethod
    def key_must_be_set(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("MIDDLEMAN_API_KEY must be a non-empty secret")
        return cleaned

    @field_validator(
        "appium_url",
        "appium_bin",
        "adb_bin",
        "adb_serial",
        "letterboxd_package",
        "letterboxd_activity",
        mode="before",
    )
    @classmethod
    def coerce_blank(cls, value: object) -> object:
        if value is None:
            return ""
        return value

    @property
    def resolved_appium_bin(self) -> str:
        return (self.appium_bin.strip() or shutil.which("appium") or "appium")

    @property
    def resolved_adb_bin(self) -> str:
        if self.adb_bin.strip():
            return self.adb_bin.strip()
        found = shutil.which("adb")
        if found:
            return found
        if _LOCAL_ADB.is_file():
            return str(_LOCAL_ADB)
        return "adb"


@lru_cache
def get_settings() -> Settings:
    return Settings()
