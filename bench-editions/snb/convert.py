"""Convert LDBC SNB Interactive v1 CsvBasic-LongDateFormatter to Omnigraph JSONL.
Usage: python -I convert.py <sf-dir> <out.jsonl>"""
import csv, json, sys, datetime as dt, os
src, out = sys.argv[1], sys.argv[2]
csv.field_size_limit(1 << 30)
def rows(rel):
    p = os.path.join(src, rel)
    with open(p, newline='', encoding='utf-8') as f:
        r = csv.reader(f, delimiter='|', quoting=csv.QUOTE_NONE)
        next(r)
        for row in r:
            yield row
def ts(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + f"{int(ms) % 1000:03d}Z"
def day(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime('%Y-%m-%d')
n = 0
with open(out, 'w', encoding='utf-8') as o:
    def node(t, d):
        global n; n += 1
        o.write(json.dumps({"type": t, "data": d}, ensure_ascii=False) + "\n")
    def edge(e, a, b, d=None):
        global n; n += 1
        o.write(json.dumps({"edge": e, "from": str(a), "to": str(b), "data": d or {}}) + "\n")
    for r in rows('dynamic/person_0_0.csv'):
        node("Person", dict(id=int(r[0]), firstName=r[1], lastName=r[2], gender=r[3], birthday=day(r[4]),
                            creationDate=ts(r[5]), locationIP=r[6], browserUsed=r[7]))
    seen = set()
    for r in rows('dynamic/post_0_0.csv'):
        i = int(r[0]); assert i not in seen; seen.add(i)
        d = dict(id=i, kind="post", creationDate=ts(r[2]), locationIP=r[3], browserUsed=r[4], length=int(r[7]))
        if r[1]: d["imageFile"] = r[1]
        if r[5]: d["language"] = r[5]
        if r[6]: d["content"] = r[6]
        node("Message", d)
    for r in rows('dynamic/comment_0_0.csv'):
        i = int(r[0]); assert i not in seen, i; seen.add(i)
        d = dict(id=i, kind="comment", creationDate=ts(r[1]), locationIP=r[2], browserUsed=r[3], length=int(r[5]))
        if r[4]: d["content"] = r[4]
        node("Message", d)
    for r in rows('dynamic/forum_0_0.csv'):
        node("Forum", dict(id=int(r[0]), title=r[1], creationDate=ts(r[2])))
    for r in rows('static/tag_0_0.csv'):
        node("Tag", dict(id=int(r[0]), name=r[1], url=r[2]))
    for r in rows('static/tagclass_0_0.csv'):
        node("TagClass", dict(id=int(r[0]), name=r[1], url=r[2]))
    for r in rows('static/place_0_0.csv'):
        node("Place", dict(id=int(r[0]), name=r[1], url=r[2], type=r[3]))
    for r in rows('static/organisation_0_0.csv'):
        node("Organisation", dict(id=int(r[0]), type=r[1], name=r[2], url=r[3]))
    for r in rows('dynamic/person_knows_person_0_0.csv'): edge("Knows", r[0], r[1], {"creationDate": ts(r[2])})
    for f in ('dynamic/post_hasCreator_person_0_0.csv', 'dynamic/comment_hasCreator_person_0_0.csv'):
        for r in rows(f): edge("HasCreator", r[0], r[1])
    for f in ('dynamic/comment_replyOf_post_0_0.csv', 'dynamic/comment_replyOf_comment_0_0.csv'):
        for r in rows(f): edge("ReplyOf", r[0], r[1])
    for r in rows('dynamic/forum_containerOf_post_0_0.csv'): edge("ContainerOf", r[0], r[1])
    for r in rows('dynamic/forum_hasMember_person_0_0.csv'): edge("HasMember", r[0], r[1], {"joinDate": ts(r[2])})
    for r in rows('dynamic/forum_hasModerator_person_0_0.csv'): edge("HasModerator", r[0], r[1])
    for f in ('dynamic/post_hasTag_tag_0_0.csv', 'dynamic/comment_hasTag_tag_0_0.csv'):
        for r in rows(f): edge("HasTag", r[0], r[1])
    for r in rows('dynamic/forum_hasTag_tag_0_0.csv'): edge("ForumHasTag", r[0], r[1])
    for r in rows('dynamic/person_hasInterest_tag_0_0.csv'): edge("HasInterest", r[0], r[1])
    for r in rows('dynamic/person_isLocatedIn_place_0_0.csv'): edge("PersonIsLocatedIn", r[0], r[1])
    for f in ('dynamic/post_isLocatedIn_place_0_0.csv', 'dynamic/comment_isLocatedIn_place_0_0.csv'):
        for r in rows(f): edge("MessageIsLocatedIn", r[0], r[1])
    for r in rows('static/organisation_isLocatedIn_place_0_0.csv'): edge("OrgIsLocatedIn", r[0], r[1])
    for r in rows('static/place_isPartOf_place_0_0.csv'): edge("IsPartOf", r[0], r[1])
    for r in rows('dynamic/person_studyAt_organisation_0_0.csv'): edge("StudyAt", r[0], r[1], {"classYear": int(r[2])})
    for r in rows('dynamic/person_workAt_organisation_0_0.csv'): edge("WorkAt", r[0], r[1], {"workFrom": int(r[2])})
    for f in ('dynamic/person_likes_post_0_0.csv', 'dynamic/person_likes_comment_0_0.csv'):
        for r in rows(f): edge("Likes", r[0], r[1], {"creationDate": ts(r[2])})
    for r in rows('static/tag_hasType_tagclass_0_0.csv'): edge("HasType", r[0], r[1])
    for r in rows('static/tagclass_isSubclassOf_tagclass_0_0.csv'): edge("IsSubclassOf", r[0], r[1])
print(n, "records")
