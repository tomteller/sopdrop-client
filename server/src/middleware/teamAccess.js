/**
 * Team-aware access checks for assets.
 *
 * The classic check on /api/v1/assets/* routes is "owner_id matches
 * req.user.id". For team-owned assets (team_id IS NOT NULL) we also
 * accept any member of the team. These helpers centralize that logic
 * so the existing routes stay readable.
 *
 * Reads (canRead) include the public/visibility logic; writes (canWrite)
 * gate by membership only.
 */

import { query } from '../models/db.js';

/**
 * Cached: is `user` a member of `teamId`?
 * The cache is per-request (set on req) so a single handler doesn't
 * hit the DB more than once per (user, team).
 */
async function isTeamMember(req, teamId) {
  if (!req.user || !teamId) return false;
  if (!req._teamMembership) req._teamMembership = new Map();
  if (req._teamMembership.has(teamId)) return req._teamMembership.get(teamId);
  const result = await query(
    'SELECT 1 FROM team_members WHERE team_id = $1 AND user_id = $2 LIMIT 1',
    [teamId, req.user.id]
  );
  const ok = result.rows.length > 0;
  req._teamMembership.set(teamId, ok);
  return ok;
}

/**
 * Resolve a team by slug → numeric id (for clients that pass the slug).
 * Returns null if not found.
 */
export async function resolveTeamIdBySlug(slug) {
  if (!slug) return null;
  const result = await query('SELECT id FROM teams WHERE slug = $1', [String(slug).toLowerCase()]);
  return result.rows[0]?.id || null;
}

/**
 * Can `req.user` read the given asset row?
 *
 * - Public assets: yes for anyone.
 * - Private/draft assets: only the owner or admin.
 * - Team-owned assets (team_id IS NOT NULL): owner, admin, or any
 *   member of the team.
 */
export async function canReadAsset(req, asset) {
  if (!asset) return false;
  if (asset.is_public === true) return true;
  if (asset.visibility === 'public' && asset.is_public !== false) return true;
  if (!req.user) return false;
  if (req.user.isAdmin) return true;
  if (asset.owner_id === req.user.id) return true;
  if (asset.team_id) return await isTeamMember(req, asset.team_id);
  return false;
}

/**
 * Can `req.user` modify (PUT/DELETE/thumbnail) the given asset row?
 *
 * Owner, admin, or team member if team-owned. Public visibility doesn't
 * grant write access.
 */
export async function canWriteAsset(req, asset) {
  if (!asset || !req.user) return false;
  if (req.user.isAdmin) return true;
  if (asset.owner_id === req.user.id) return true;
  if (asset.team_id) return await isTeamMember(req, asset.team_id);
  return false;
}
