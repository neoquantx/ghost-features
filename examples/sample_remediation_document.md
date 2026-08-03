# Ghost Feature Detected: customer_email_domain

**Document URN:** `urn:li:document:shared-601fbeed-62c2-4e89-9984-dcc137d8825c`  
**Type:** Analysis  
**Status:** PUBLISHED  
**Created by:** `urn:li:corpuser:__ingestion`

---

Feature `urn:li:mlFeature:(customer_risk_features,customer_email_domain)` is ghosted: expected column 'cust_email' is missing from its source table.

Impacted production models:
- urn:li:mlModel:(urn:li:dataPlatform:mlflow,fraud_risk_model_v1,PROD)

These models may be silently consuming stale or missing data.

---

**Related assets:**
- `urn:li:mlFeature:(customer_risk_features,customer_email_domain)`
- `urn:li:mlModel:(urn:li:dataPlatform:mlflow,fraud_risk_model_v1,PROD)`
