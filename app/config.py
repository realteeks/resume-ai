from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_secret_key: str = "dev-secret-change-me"
    base_url: str = "http://localhost:8000"
    environment: str = "production"  # SAFE DEFAULT: docs/health/cookies locked down
    trust_proxy: bool = False  # set true only behind a trusted proxy (e.g. Render)

    # --- Billing (Razorpay) ---
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    currency: str = "INR"
    credit_price: float = 49.0        # price of ONE credit (env: CREDIT_PRICE)
    credit_packs: str = "3,5,10"      # purchasable pack sizes (comma-separated)
    pro_price: float = 299.0          # PRO monthly pass price (env: PRO_PRICE)
    pro_monthly_credits: int = 30     # credits granted with a PRO month
    signup_credits: int = 3           # free credits for new accounts

    google_client_id: str = ""
    google_client_secret: str = ""

    # Single key (back-compat) and/or a comma-separated list of keys.
    # Provide multiple free-tier keys to multiply your effective rate limit.
    groq_api_key: str = ""
    groq_api_keys: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Gemini / Gemma (Google AI Studio). Multiple keys rotate just like Groq.
    gemini_api_key: str = ""
    gemini_api_keys: str = ""
    gemini_models: str = "gemma-4-31b-it,gemma-4-26b-a4b-it"

    # Which provider to try first ("gemini" or "groq"). The other is fallback.
    primary_provider: str = "gemini"

    database_url: str = "sqlite:///./placeholderai.db"

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.base_url}/auth/callback"

    @property
    def auth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower().startswith("prod")

    @property
    def payments_configured(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def cookies_secure(self) -> bool:
        """Secure cookies whenever we're served over HTTPS (independent of the
        environment string, so a fumbled env var can't downgrade a live site)."""
        return self.base_url.strip().lower().startswith("https")

    @property
    def dev_tools_enabled(self) -> bool:
        """Dev-only shortcuts (dev-login, dev credit grant) are available ONLY
        when neither real auth NOR real payments are configured. This is
        env-string-independent, so a real deploy (which has keys) can never
        expose them — even if ENVIRONMENT is misconfigured."""
        return not (self.auth_configured or self.payments_configured)

    @property
    def credit_pack_sizes(self) -> list[int]:
        sizes = []
        for part in self.credit_packs.split(","):
            part = part.strip()
            if part.isdigit() and int(part) > 0:
                sizes.append(int(part))
        return sizes or [3, 5, 10]

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for v in (x.strip() for x in values):
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out

    @property
    def groq_keys(self) -> list[str]:
        """All configured Groq keys, de-duplicated, order preserved."""
        return self._dedupe([self.groq_api_key, *self.groq_api_keys.split(",")])

    @property
    def gemini_keys(self) -> list[str]:
        """All configured Gemini/Gemma keys, de-duplicated, order preserved."""
        return self._dedupe([self.gemini_api_key, *self.gemini_api_keys.split(",")])

    @property
    def gemini_model_list(self) -> list[str]:
        return self._dedupe(self.gemini_models.split(","))

    @property
    def llm_configured(self) -> bool:
        return bool(self.groq_keys or self.gemini_keys)


settings = Settings()
