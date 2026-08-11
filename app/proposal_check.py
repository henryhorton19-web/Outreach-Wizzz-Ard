def proposal_notes(body: str, ctx: dict) -> list[str]:
    what=str((ctx or {}).get('what_they_do') or '')
    if not what:return []
    low=(body or '').lower(); notes=[]
    own=('build the system to find','find companies, research','outreach system','write the outreach')
    if any(x in low for x in own) and not any(x in what.lower() for x in ('outreach','outbound','sales','marketing','leads','crm')):
        notes.append(f"Act on THEIR business ({what!r}), rather than rebuilding your outreach project.")
    if any(x in low for x in ('just as i did for','which is work i have done before at','as i did at')):
        notes.append('Lead with the action, not an employer credential.')
    return notes[:2]
