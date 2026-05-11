/**
 * Team Library Routes
 *
 * Read-only browse endpoints for team-owned assets, used by the Houdini
 * panel when configured to talk to an on-prem server. All routes are
 * scoped to a team (via :slug) and require team membership.
 *
 * Mounted at /api/v1/teams to share the namespace with teams.js. Routes
 * here all live under /:slug/library/* to avoid collisions.
 *
 * Phase 0 ships read endpoints only. Writes (publish/edit/delete) for
 * team-owned assets land in Phase 1, when the panel actually swaps over.
 */

import { Router } from 'express';
import crypto from 'crypto';
import { query } from '../models/db.js';
import { authenticate } from '../middleware/auth.js';
import { NotFoundError, ForbiddenError, ValidationError, ConflictError } from '../middleware/errorHandler.js';
import { sanitizePlainText } from '../middleware/security.js';
import { toPublicUrl } from '../services/storage.js';

const router = Router();

// ─── Helpers ────────────────────────────────────────────────────────────

/**
 * Resolve a team by slug and verify the authenticated user is a member.
 * Sets req.team = { id, slug, name, role } and continues.
 *
 * 404 if team doesn't exist (no information leak about private teams).
 * 403 if user is not a member.
 */
async function requireTeamMember(req, res, next) {
  try {
    const teamRes = await query(
      'SELECT id, team_id, slug, name FROM teams WHERE slug = $1',
      [req.params.slug]
    );
    if (teamRes.rows.length === 0) {
      throw new NotFoundError('Team not found');
    }
    const team = teamRes.rows[0];

    const memberRes = await query(
      'SELECT role FROM team_members WHERE team_id = $1 AND user_id = $2',
      [team.id, req.user.id]
    );
    if (memberRes.rows.length === 0 && !req.user.isAdmin) {
      // Same 404 as missing-team to avoid leaking team existence
      throw new NotFoundError('Team not found');
    }

    req.team = {
      id: team.id,
      teamId: team.team_id,
      slug: team.slug,
      name: team.name,
      role: memberRes.rows[0]?.role || (req.user.isAdmin ? 'admin' : 'member'),
    };
    next();
  } catch (err) {
    next(err);
  }
}

/**
 * Compute a strong ETag for a result set. We hash a stable digest of the
 * row count, max(updated_at), and the filter signature — so identical
 * filters over an unchanged dataset always produce the same ETag without
 * needing to serialize the response body first.
 */
function computeListEtag({ count, lastUpdated, filterKey }) {
  const h = crypto.createHash('sha1');
  h.update(String(count));
  h.update('|');
  h.update(String(lastUpdated || ''));
  h.update('|');
  h.update(filterKey || '');
  return `"${h.digest('hex')}"`;
}

/**
 * Set list-response cache headers. Per-team data is per-user-private, so:
 *   - Cache-Control: private, must-revalidate, max-age=0
 *     → client may cache, but must revalidate every time (cheap with ETag)
 *   - ETag set so revalidation returns 304 with empty body
 */
function setListCacheHeaders(res, etag) {
  res.setHeader('Cache-Control', 'private, must-revalidate, max-age=0');
  res.setHeader('ETag', etag);
}

function clamp(n, min, max, fallback) {
  const v = parseInt(n);
  if (!Number.isFinite(v)) return fallback;
  return Math.max(min, Math.min(max, v));
}

// ─── GET /:slug/library ─────────────────────────────────────────────────
//
// Paginated browse of team-owned assets. Returns the rich row shape the
// Houdini panel expects (mirrors /api/v1/assets but team-scoped, with
// extra metadata fields the panel reads directly).

router.get('/:slug/library', authenticate, requireTeamMember, async (req, res, next) => {
  try {
    const {
      q,
      context,
      type,
      tags,
      sort = 'updated',
      since,
    } = req.query;
    const limit = clamp(req.query.limit, 1, 200, 100);
    const offset = clamp(req.query.offset, 0, 1_000_000, 0);

    const where = ['a.team_id = $1', 'COALESCE(a.is_deprecated, false) = false'];
    const params = [req.team.id];
    let p = 2;

    if (q) {
      where.push(`(a.name ILIKE $${p} OR a.description ILIKE $${p} OR $${p + 1} = ANY(a.tags))`);
      params.push(`%${q}%`, q.toLowerCase());
      p += 2;
    }
    if (context) {
      where.push(`a.houdini_context = $${p}`);
      params.push(String(context).toLowerCase());
      p++;
    }
    if (type) {
      where.push(`a.asset_type = $${p}`);
      params.push(String(type).toLowerCase());
      p++;
    }
    if (tags) {
      const list = String(tags).split(',').map(t => t.trim().toLowerCase()).filter(Boolean);
      if (list.length > 0) {
        where.push(`a.tags && $${p}::text[]`);
        params.push(list);
        p++;
      }
    }
    if (since) {
      where.push(`a.updated_at > $${p}`);
      params.push(since);
      p++;
    }

    let orderBy = 'ORDER BY a.updated_at DESC';
    if (sort === 'name') orderBy = 'ORDER BY a.name ASC';
    else if (sort === 'recent' || sort === 'created') orderBy = 'ORDER BY a.created_at DESC';
    else if (sort === 'downloads' || sort === 'popular') orderBy = 'ORDER BY a.download_count DESC';

    const whereSql = where.join(' AND ');

    // Count + max(updated_at) for ETag, in one round-trip
    const summary = await query(
      `SELECT COUNT(*)::int AS total, MAX(a.updated_at) AS last_updated
       FROM assets a WHERE ${whereSql}`,
      params
    );
    const total = summary.rows[0].total;
    const lastUpdated = summary.rows[0].last_updated;

    const filterKey = JSON.stringify({ q, context, type, tags, sort, since, limit, offset });
    const etag = computeListEtag({ count: total, lastUpdated, filterKey });
    setListCacheHeaders(res, etag);

    if (req.fresh) {
      // If-None-Match matched — body is unchanged
      return res.status(304).end();
    }

    if (total === 0) {
      return res.json({
        assets: [],
        collectionMap: {},
        total: 0,
        limit,
        offset,
        lastUpdated,
      });
    }

    const result = await query(
      `SELECT
         a.id, a.asset_id, a.name, a.slug,
         u.username AS owner, u.avatar_url AS owner_avatar,
         a.asset_type, a.houdini_context,
         a.description, a.readme, a.license, a.tags,
         a.latest_version, a.download_count,
         a.metadata, a.icon,
         a.created_at, a.updated_at,
         v.thumbnail_url, v.preview_url,
         v.node_count, v.node_names, v.file_hash, v.file_size, v.file_path
       FROM assets a
       JOIN users u ON a.owner_id = u.id
       LEFT JOIN versions v ON a.latest_version_id = v.id
       WHERE ${whereSql}
       ${orderBy}
       LIMIT $${p} OFFSET $${p + 1}`,
      [...params, limit, offset]
    );

    const assetRows = result.rows;
    const assetIds = assetRows.map(r => r.id);

    // Collection membership (folders) for the returned page only
    let collectionMap = {};
    if (assetIds.length > 0) {
      const folderRes = await query(
        `SELECT a.id AS asset_id, f.folder_id, f.slug AS folder_slug
         FROM assets a
         JOIN user_folders f ON a.folder_id = f.id
         WHERE a.id = ANY($1::int[]) AND f.team_id = $2`,
        [assetIds, req.team.id]
      );
      for (const row of folderRes.rows) {
        const key = row.folder_id;
        if (!collectionMap[key]) collectionMap[key] = [];
        collectionMap[key].push(row.asset_id);
      }
    }

    res.json({
      assets: assetRows.map(a => ({
        id: a.asset_id,
        dbId: a.id,
        name: a.name,
        slug: `${a.owner}/${a.slug}`,
        owner: a.owner,
        ownerAvatar: a.owner_avatar,
        type: a.asset_type,
        context: a.houdini_context,
        description: a.description,
        readme: a.readme,
        license: a.license,
        tags: a.tags || [],
        latestVersion: a.latest_version,
        downloadCount: a.download_count,
        metadata: a.metadata || {},
        icon: a.icon,
        nodeCount: a.node_count,
        nodeNames: a.node_names || [],
        fileHash: a.file_hash,
        fileSize: a.file_size,
        thumbnailUrl: toPublicUrl(a.thumbnail_url),
        previewUrl: toPublicUrl(a.preview_url),
        downloadUrl: toPublicUrl(a.file_path),
        createdAt: a.created_at,
        updatedAt: a.updated_at,
      })),
      collectionMap,
      total,
      limit,
      offset,
      lastUpdated,
    });
  } catch (err) {
    next(err);
  }
});

// ─── GET /:slug/library/collections ─────────────────────────────────────
//
// The folder/collection sidebar. Flat list — caller builds the tree.

router.get('/:slug/library/collections', authenticate, requireTeamMember, async (req, res, next) => {
  try {
    const summary = await query(
      `SELECT COUNT(*)::int AS total, MAX(updated_at) AS last_updated
       FROM user_folders WHERE team_id = $1`,
      [req.team.id]
    );
    const etag = computeListEtag({
      count: summary.rows[0].total,
      lastUpdated: summary.rows[0].last_updated,
      filterKey: 'collections',
    });
    setListCacheHeaders(res, etag);
    if (req.fresh) return res.status(304).end();

    const result = await query(
      `SELECT id, folder_id, name, slug, description, color, icon,
              parent_id, position, asset_count, created_at, updated_at
       FROM user_folders
       WHERE team_id = $1
       ORDER BY position ASC, name ASC`,
      [req.team.id]
    );

    // The panel renders the folder tree by matching child.parentId
    // against parent.id (UUID). The DB stores parent_id as the integer
    // FK to user_folders.id, so we translate to the parent's UUID
    // before sending. Integer kept as parentDbId for back-compat.
    const idToUuid = new Map();
    for (const row of result.rows) idToUuid.set(row.id, row.folder_id);

    res.json({
      collections: result.rows.map(c => ({
        id: c.folder_id,
        dbId: c.id,
        name: c.name,
        slug: c.slug,
        description: c.description,
        color: c.color,
        icon: c.icon,
        parentId: c.parent_id == null ? null : (idToUuid.get(c.parent_id) || null),
        parentDbId: c.parent_id,
        position: c.position,
        assetCount: c.asset_count,
        createdAt: c.created_at,
        updatedAt: c.updated_at,
      })),
      total: summary.rows[0].total,
    });
  } catch (err) {
    next(err);
  }
});

// ─── GET /:slug/library/tags ────────────────────────────────────────────
//
// Tag counts within the team library. Powers the filter chips.

router.get('/:slug/library/tags', authenticate, requireTeamMember, async (req, res, next) => {
  try {
    const limit = clamp(req.query.limit, 1, 500, 200);
    const sort = req.query.sort === 'alpha' ? 'alpha' : 'popular';

    // unnest gives us one row per tag occurrence; group + count
    const result = await query(
      `SELECT tag, COUNT(*)::int AS count
       FROM (
         SELECT UNNEST(tags) AS tag
         FROM assets
         WHERE team_id = $1 AND COALESCE(is_deprecated, false) = false
       ) t
       GROUP BY tag
       ORDER BY ${sort === 'alpha' ? 'tag ASC' : 'count DESC, tag ASC'}
       LIMIT $2`,
      [req.team.id, limit]
    );

    const etag = computeListEtag({
      count: result.rows.length,
      lastUpdated: '', // no good signal here; revalidate based on content
      filterKey: `tags:${sort}:${limit}`,
    });
    setListCacheHeaders(res, etag);
    if (req.fresh) return res.status(304).end();

    res.json({
      tags: result.rows.map(r => ({ tag: r.tag, count: r.count })),
    });
  } catch (err) {
    next(err);
  }
});

// ─── GET /:slug/library/assets/:assetId ─────────────────────────────────
//
// Single team-asset by UUID. The panel uses this when a card is clicked
// (no need to re-fetch the whole list).

router.get('/:slug/library/assets/:assetId', authenticate, requireTeamMember, async (req, res, next) => {
  try {
    const result = await query(
      `SELECT
         a.id, a.asset_id, a.name, a.slug,
         u.username AS owner, u.avatar_url AS owner_avatar,
         a.asset_type, a.houdini_context,
         a.description, a.readme, a.license, a.tags,
         a.latest_version, a.download_count, a.metadata, a.icon,
         a.created_at, a.updated_at,
         f.folder_id AS folder_uuid, f.slug AS folder_slug, f.name AS folder_name,
         v.thumbnail_url, v.preview_url,
         v.node_count, v.node_names, v.file_hash, v.file_size, v.file_path, v.code
       FROM assets a
       JOIN users u ON a.owner_id = u.id
       LEFT JOIN versions v ON a.latest_version_id = v.id
       LEFT JOIN user_folders f ON a.folder_id = f.id
       WHERE a.team_id = $1 AND a.asset_id = $2`,
      [req.team.id, req.params.assetId]
    );
    if (result.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }
    const a = result.rows[0];
    res.setHeader('Cache-Control', 'private, must-revalidate, max-age=0');
    // Fold folder membership into the response. The list endpoint
    // returns this via collectionMap; the single-asset endpoint hadn't,
    // which is why the Edit Details collection picker showed "(none)"
    // even on assets that lived in a folder.
    const folder = a.folder_uuid
      ? { id: a.folder_uuid, slug: a.folder_slug, name: a.folder_name }
      : null;
    res.json({
      id: a.asset_id,
      dbId: a.id,
      name: a.name,
      slug: `${a.owner}/${a.slug}`,
      owner: a.owner,
      ownerAvatar: a.owner_avatar,
      type: a.asset_type,
      context: a.houdini_context,
      description: a.description,
      readme: a.readme,
      license: a.license,
      tags: a.tags || [],
      latestVersion: a.latest_version,
      downloadCount: a.download_count,
      metadata: a.metadata || {},
      icon: a.icon,
      nodeCount: a.node_count,
      nodeNames: a.node_names || [],
      code: a.code,
      fileHash: a.file_hash,
      fileSize: a.file_size,
      thumbnailUrl: toPublicUrl(a.thumbnail_url),
      previewUrl: toPublicUrl(a.preview_url),
      downloadUrl: toPublicUrl(a.file_path),
      createdAt: a.created_at,
      updatedAt: a.updated_at,
      folder,
    });
  } catch (err) {
    next(err);
  }
});

// ─── POST /:slug/library/assets/:assetId/use ────────────────────────────
//
// Record that the user pasted/installed this asset. Best-effort —
// errors are swallowed by the panel side, never blocks paste.

router.post('/:slug/library/assets/:assetId/use', authenticate, requireTeamMember, async (req, res, next) => {
  try {
    const result = await query(
      `UPDATE assets SET download_count = download_count + 1
       WHERE team_id = $1 AND asset_id = $2
       RETURNING download_count`,
      [req.team.id, req.params.assetId]
    );
    if (result.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }
    res.json({ downloadCount: result.rows[0].download_count });
  } catch (err) {
    next(err);
  }
});

// ─── Folder/collection CRUD (team-scoped) ───────────────────────────────
//
// The panel sidebar treats team folders as first-class shared resources.
// All members can read; any member can write (create, rename, delete).

function slugifyName(name) {
  return String(name).toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 100);
}

router.post('/:slug/library/collections', authenticate, requireTeamMember, async (req, res, next) => {
  try {
    const name = sanitizePlainText(req.body.name, 100);
    if (!name) throw new ValidationError('name is required');
    const description = sanitizePlainText(req.body.description, 500) || null;
    const color = req.body.color ? String(req.body.color).slice(0, 7) : null;
    const icon = req.body.icon ? String(req.body.icon).slice(0, 50) : null;
    const parentSlug = req.body.parentSlug || null;
    const folderSlug = slugifyName(name);

    let parentId = null;
    let parentUuid = null;
    if (parentSlug) {
      const p = await query(
        'SELECT id, folder_id FROM user_folders WHERE team_id = $1 AND slug = $2',
        [req.team.id, parentSlug]
      );
      if (p.rows.length === 0) throw new NotFoundError('Parent folder not found');
      parentId = p.rows[0].id;
      parentUuid = p.rows[0].folder_id;
    }

    let result;
    try {
      result = await query(
        `INSERT INTO user_folders (user_id, team_id, name, slug, description, color, icon, parent_id, position)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 0)
         RETURNING *`,
        [req.user.id, req.team.id, name, folderSlug, description, color, icon, parentId]
      );
    } catch (e) {
      if (e.code === '23505') throw new ConflictError('A folder with this name already exists');
      throw e;
    }
    const f = result.rows[0];
    res.status(201).json({
      id: f.folder_id,
      dbId: f.id,
      name: f.name,
      slug: f.slug,
      description: f.description,
      color: f.color,
      icon: f.icon,
      parentId: parentUuid,
      parentDbId: f.parent_id,
      position: f.position,
      assetCount: 0,
      createdAt: f.created_at,
      updatedAt: f.updated_at,
    });
  } catch (err) {
    next(err);
  }
});

router.put('/:slug/library/collections/:folderId', authenticate, requireTeamMember, async (req, res, next) => {
  try {
    const updates = [];
    const params = [];
    let p = 1;

    if (req.body.name !== undefined) {
      const name = sanitizePlainText(req.body.name, 100);
      if (!name) throw new ValidationError('name is required');
      updates.push(`name = $${p++}`); params.push(name);
      updates.push(`slug = $${p++}`); params.push(slugifyName(name));
    }
    if (req.body.description !== undefined) {
      updates.push(`description = $${p++}`);
      params.push(sanitizePlainText(req.body.description, 500) || null);
    }
    if (req.body.color !== undefined) {
      updates.push(`color = $${p++}`); params.push(String(req.body.color).slice(0, 7));
    }
    if (req.body.icon !== undefined) {
      updates.push(`icon = $${p++}`); params.push(String(req.body.icon).slice(0, 50));
    }
    if (req.body.position !== undefined) {
      updates.push(`position = $${p++}`); params.push(parseInt(req.body.position) || 0);
    }
    // Optional parent move. parentSlug=null clears the parent (folder
    // becomes a root); a slug looks up the matching team folder. Used
    // by the NAS-migration script to repair hierarchy when folders were
    // created flat by an earlier run.
    if (req.body.parentSlug !== undefined) {
      let nextParentId = null;
      if (req.body.parentSlug !== null && req.body.parentSlug !== '') {
        const lookup = await query(
          'SELECT id FROM user_folders WHERE team_id = $1 AND slug = $2',
          [req.team.id, req.body.parentSlug]
        );
        if (lookup.rows.length === 0) throw new NotFoundError('Parent folder not found');
        nextParentId = lookup.rows[0].id;
        // Reject self-parenting.
        const self = await query(
          'SELECT id FROM user_folders WHERE team_id = $1 AND folder_id = $2',
          [req.team.id, req.params.folderId]
        );
        if (self.rows.length > 0 && self.rows[0].id === nextParentId) {
          throw new ValidationError('A folder cannot be its own parent');
        }
      }
      updates.push(`parent_id = $${p++}`); params.push(nextParentId);
    }
    if (updates.length === 0) throw new ValidationError('No fields to update');
    updates.push(`updated_at = NOW()`);

    const result = await query(
      `UPDATE user_folders SET ${updates.join(', ')}
       WHERE team_id = $${p++} AND folder_id = $${p++}
       RETURNING *`,
      [...params, req.team.id, req.params.folderId]
    );
    if (result.rows.length === 0) throw new NotFoundError('Folder not found');
    const f = result.rows[0];

    // Translate the updated parent_id (integer FK) → parent's UUID so
    // the panel's tree-building (which matches child.parentId against
    // parent.id) sees a usable value.
    let parentUuid = null;
    if (f.parent_id != null) {
      const pp = await query(
        'SELECT folder_id FROM user_folders WHERE id = $1',
        [f.parent_id]
      );
      if (pp.rows.length > 0) parentUuid = pp.rows[0].folder_id;
    }

    res.json({
      id: f.folder_id, dbId: f.id, name: f.name, slug: f.slug,
      description: f.description, color: f.color, icon: f.icon,
      parentId: parentUuid, parentDbId: f.parent_id,
      position: f.position,
      assetCount: f.asset_count,
      createdAt: f.created_at, updatedAt: f.updated_at,
    });
  } catch (err) {
    next(err);
  }
});

router.delete('/:slug/library/collections/:folderId', authenticate, requireTeamMember, async (req, res, next) => {
  try {
    const result = await query(
      'DELETE FROM user_folders WHERE team_id = $1 AND folder_id = $2 RETURNING id',
      [req.team.id, req.params.folderId]
    );
    if (result.rows.length === 0) throw new NotFoundError('Folder not found');
    res.json({ success: true });
  } catch (err) {
    next(err);
  }
});

// ─── Trash / recovery ───────────────────────────────────────────────────
//
// Soft-deleted team assets (is_deprecated = true) live forever on disk
// until explicitly purged. Any team member can restore an asset they or
// the team owns; only team admins/owners can purge (which removes the
// row and the file).

router.get('/:slug/library/trash', authenticate, requireTeamMember, async (req, res, next) => {
  try {
    const result = await query(
      `SELECT a.id, a.asset_id, a.name, a.slug, a.asset_type, a.houdini_context,
              a.description, a.tags, a.updated_at, a.deprecated_message,
              u.username AS owner, u.avatar_url AS owner_avatar,
              v.thumbnail_url, v.file_size
       FROM assets a
       JOIN users u ON a.owner_id = u.id
       LEFT JOIN versions v ON a.latest_version_id = v.id
       WHERE a.team_id = $1 AND a.is_deprecated = true
       ORDER BY a.updated_at DESC`,
      [req.team.id]
    );
    res.setHeader('Cache-Control', 'private, must-revalidate, max-age=0');
    res.json({
      assets: result.rows.map(a => ({
        id: a.asset_id,
        dbId: a.id,
        name: a.name,
        slug: `${a.owner}/${a.slug}`,
        owner: a.owner,
        ownerAvatar: a.owner_avatar,
        type: a.asset_type,
        context: a.houdini_context,
        description: a.description,
        tags: a.tags || [],
        thumbnailUrl: toPublicUrl(a.thumbnail_url),
        fileSize: a.file_size,
        deletedAt: a.updated_at,
        deletedReason: a.deprecated_message,
      })),
      total: result.rows.length,
    });
  } catch (err) {
    next(err);
  }
});

router.post('/:slug/library/assets/:assetId/restore', authenticate, requireTeamMember, async (req, res, next) => {
  try {
    // If the user has published a NEW asset with the same owner+slug
    // since this one was trashed, the partial unique index would
    // reject the restore with a duplicate-key error. Detect that here
    // so we can return a clean 409 instead of a 500.
    const conflictCheck = await query(
      `SELECT live.id AS live_id, trashed.name AS trashed_name
         FROM assets trashed
         LEFT JOIN assets live
           ON live.owner_id = trashed.owner_id
          AND live.slug = trashed.slug
          AND live.id <> trashed.id
          AND COALESCE(live.is_deprecated, false) = false
        WHERE trashed.team_id = $1
          AND trashed.asset_id = $2
          AND trashed.is_deprecated = true`,
      [req.team.id, req.params.assetId]
    );
    if (conflictCheck.rows.length === 0) {
      throw new NotFoundError('Trashed asset not found');
    }
    if (conflictCheck.rows[0].live_id) {
      throw new ConflictError(
        `Cannot restore: an asset named "${conflictCheck.rows[0].trashed_name}" ` +
        `already exists. Rename or delete it first.`
      );
    }

    const result = await query(
      `UPDATE assets
         SET is_deprecated = false,
             deprecated_message = NULL,
             updated_at = NOW()
       WHERE team_id = $1 AND asset_id = $2 AND is_deprecated = true
       RETURNING asset_id, name, slug`,
      [req.team.id, req.params.assetId]
    );
    if (result.rows.length === 0) {
      throw new NotFoundError('Trashed asset not found');
    }
    res.json({
      success: true,
      id: result.rows[0].asset_id,
      name: result.rows[0].name,
    });
  } catch (err) {
    next(err);
  }
});

router.delete('/:slug/library/assets/:assetId/purge', authenticate, requireTeamMember, async (req, res, next) => {
  try {
    // Purge requires team admin/owner — too destructive for any member.
    if (!['owner', 'admin'].includes(req.team.role) && !req.user.isAdmin) {
      throw new ForbiddenError('Only team admins can permanently delete assets');
    }
    // Find the asset + its file paths so we can remove from storage too.
    const found = await query(
      `SELECT a.id, a.asset_id, a.name,
              v.file_path, v.thumbnail_url
       FROM assets a
       LEFT JOIN versions v ON a.latest_version_id = v.id
       WHERE a.team_id = $1 AND a.asset_id = $2`,
      [req.team.id, req.params.assetId]
    );
    if (found.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }
    const a = found.rows[0];

    // Cascade-delete via FKs handles versions, saved_assets, etc. The asset
    // file and thumbnail are on disk — best-effort cleanup, ignore failures
    // (the orphaned blob is harmless, just costs space).
    await query('DELETE FROM assets WHERE id = $1', [a.id]);
    try {
      const { default: storage } = await import('../services/storage.js');
      const fileKey = storage.pathToKey(a.file_path);
      if (fileKey) await storage.remove(fileKey);
      const thumbKey = storage.pathToKey(a.thumbnail_url);
      if (thumbKey) await storage.remove(thumbKey);
    } catch {
      // ignore storage cleanup errors
    }

    res.json({ success: true, id: a.asset_id, name: a.name });
  } catch (err) {
    next(err);
  }
});

// ─── GET /:slug/share/latest ────────────────────────────────────────────
//
// Returns the most-recent non-expired team-scoped share. The Houdini
// panel's Quick Copy on workstation A POSTs to /share with teamSlug;
// workstation B (in the same team) hits this endpoint when its local
// clipboard is empty so the user doesn't have to copy the 8-char
// share code across machines. Walks the index
// idx_temp_shares_team_latest.

router.get('/:slug/share/latest', authenticate, requireTeamMember, async (req, res, next) => {
  try {
    const result = await query(
      `SELECT share_code, name, houdini_context, node_count, node_names,
              created_by, expires_at, created_at
         FROM temp_shares
        WHERE team_id = $1 AND expires_at > NOW()
        ORDER BY created_at DESC
        LIMIT 1`,
      [req.team.id]
    );
    if (result.rows.length === 0) {
      throw new NotFoundError('No active team share');
    }
    const s = result.rows[0];
    let createdByUsername = null;
    if (s.created_by) {
      const u = await query('SELECT username FROM users WHERE id = $1', [s.created_by]);
      if (u.rows.length) createdByUsername = u.rows[0].username;
    }
    res.json({
      shareCode: s.share_code,
      name: s.name,
      context: s.houdini_context,
      nodeCount: s.node_count,
      nodeNames: s.node_names || [],
      createdBy: createdByUsername,
      createdAt: s.created_at,
      expiresAt: s.expires_at,
    });
  } catch (err) {
    next(err);
  }
});

// ─── GET /:slug/library/stats ───────────────────────────────────────────
//
// Footer stats: asset count, total size, collection count.

router.get('/:slug/library/stats', authenticate, requireTeamMember, async (req, res, next) => {
  try {
    const result = await query(
      `SELECT
         (SELECT COUNT(*)::int FROM assets
            WHERE team_id = $1 AND COALESCE(is_deprecated, false) = false) AS asset_count,
         (SELECT COUNT(*)::int FROM user_folders WHERE team_id = $1) AS collection_count,
         (SELECT COALESCE(SUM(v.file_size), 0)::bigint
            FROM assets a
            JOIN versions v ON a.latest_version_id = v.id
            WHERE a.team_id = $1 AND COALESCE(a.is_deprecated, false) = false) AS total_size,
         (SELECT MAX(a.updated_at)
            FROM assets a WHERE a.team_id = $1) AS last_updated`,
      [req.team.id]
    );
    const row = result.rows[0];
    res.setHeader('Cache-Control', 'private, must-revalidate, max-age=0');
    res.json({
      assetCount: row.asset_count,
      collectionCount: row.collection_count,
      totalSizeBytes: Number(row.total_size),
      totalSizeMb: Number(row.total_size) / (1024 * 1024),
      lastUpdated: row.last_updated,
    });
  } catch (err) {
    next(err);
  }
});

export default router;
