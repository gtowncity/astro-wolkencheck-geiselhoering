# Safety design

Priority is confirmed or latched RED, confirmed or latched YELLOW, missing required data, then GREEN. Required sources are fresh complete DWD RV, fresh complete DWD CAP and healthy local persistence. A source failure never clears a known hazard. Clearing needs the minimum hold time plus two fresh complete clear cycles. Acknowledgement suppresses alarm repetition only; it does not change evidence, risk or action.

Relevant active thunderstorm and heavy-rain CAP warnings are hardware RED independent of CAP severity. Future or expired warnings are excluded by `is_in_force`.

SQLite corruption or write failure forces `LOCAL_PERSISTENCE=FAILED`, therefore GREEN is impossible.

Die Anwendung ist kein zertifiziertes Schutz- oder Unwetterwarnsystem.
