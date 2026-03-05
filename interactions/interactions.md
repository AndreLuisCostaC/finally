/doc-review PLAN.md

=> I've updated the comments at the end of PLAN.md with my feedback and indicated what should be the principles to be followed in the simplifications section. Please now review the remaining issues, and incorporate the solutions throughout PLAN.md, letting me know any questions 

### to call an agent we need only to refers to the agent name (Ex. reviewer plan below)
=> use the review plan sub-agent to carry out a review

=> codex exec "please review the file planning/PLAN.md and write your feedback to planning/REVIEW_CODEX.md"

=> codex exec "please fix the ambiguity between frontend and backend given the precedence to the backend definitions, and fix the contradiction in the test strategy, use the TDD best practices definition. Apply these corections to planning/PLAN.md
"


=> codex exec "ok, the section 8. API Endpoints stablish the endpoints Market Data, Portfolio, Watchlist, Chat and System. Use their respective descriptions to define the request format, response format, Status Codes, Authentication and Error Handling. Use the Pydantic schemas to implement the API contracts. Ensure to align the backend and frontend with the same API signatures also refers to plannng/api_lifecycle_and_validation.md to Aply the corrections in the planning/PLAN.md
"

=> use your codex-review subagent to carry out a review of planning/PLAN.md   

=> use your change-reviewer subagent to review changes since the last commit

=> please write a concise README.md for the project