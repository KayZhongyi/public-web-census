# LinkedIn connector

Use `collect linkedin` for visible posts on a LinkedIn company page or personal profile when the business question needs public content, messaging patterns, posting cadence, or visible engagement evidence.

## Supported inputs

- `https://www.linkedin.com/company/<company>/`
- `https://www.linkedin.com/in/<person>/`

The connector opens the matching posts or recent-activity view in the user's authorized Chrome session. It reads visible post text, relative publication label, reactions, comment count, repost/share count, impressions when visible, permalink, and stable local ID.

```bash
./public-web-census collect linkedin \
  --workspace runs/target \
  --company "Target" \
  --profile "https://www.linkedin.com/company/target/" \
  --output runs/target-linkedin
```

## Human control

- Sign in to LinkedIn directly in Chrome. The connector never requests or stores the password.
- If LinkedIn asks for a security check, complete it yourself and rerun the collection.
- Confirm that the company or person is the intended research target before analysis.

## Current boundary

- Company and personal-profile posts: supported.
- Visible reaction, comment-count, repost/share, and impression counters: captured when exposed.
- Comment and reply bodies: not implemented. A nonblank `comments_count` does not mean comment text was collected.
- People search and profile biography fields are not part of the standard evidence bundle. Use them only during target verification when the business question requires them.
- LinkedIn may personalize and virtualize feeds. Treat the result as a best-effort visible census with a stated cutoff, not proof that every historical post exists in the capture.
