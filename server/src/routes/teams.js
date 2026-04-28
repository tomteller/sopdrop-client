/**
 * Teams Routes
 *
 * Handles team creation, membership, and team asset libraries.
 * Teams allow studios/groups to share a common asset library.
 */

import { Router } from 'express';
import crypto from 'crypto';
import { query } from '../models/db.js';
import { authenticate, optionalAuth } from '../middleware/auth.js';
import { ValidationError, NotFoundError, ForbiddenError } from '../middleware/errorHandler.js';

const router = Router();

/**
 * Helper: Check if user is a member of a team
 */
async function getTeamMembership(teamId, userId) {
  const result = await query(
    'SELECT role FROM team_members WHERE team_id = $1 AND user_id = $2',
    [teamId, userId]
  );
  return result.rows[0] || null;
}

/**
 * Helper: Check if user can manage team (owner or admin)
 */
async function canManageTeam(teamId, userId) {
  const membership = await getTeamMembership(teamId, userId);
  return membership && ['owner', 'admin'].includes(membership.role);
}

/**
 * Helper: Slugify team name
 */
function slugify(text) {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .substring(0, 50);
}

// ============================================
// Team CRUD
// ============================================

/**
 * GET /teams
 * List user's teams
 */
router.get('/', authenticate, async (req, res, next) => {
  try {
    const result = await query(`
      SELECT t.*, tm.role as my_role
      FROM teams t
      JOIN team_members tm ON t.id = tm.team_id
      WHERE tm.user_id = $1
      ORDER BY t.name
    `, [req.user.id]);

    res.json({
      teams: result.rows.map(t => ({
        id: t.team_id,
        name: t.name,
        slug: t.slug,
        description: t.description,
        memberCount: t.member_count,
        assetCount: t.asset_count,
        myRole: t.my_role,
        isPublic: t.is_public,
        createdAt: t.created_at,
      })),
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /teams
 * Create a new team
 */
router.post('/', authenticate, async (req, res, next) => {
  try {
    const { name, description, isPublic } = req.body;

    if (!name || name.trim().length < 2) {
      throw new ValidationError('Team name must be at least 2 characters');
    }

    const slug = slugify(name);

    // Check slug uniqueness
    const existing = await query('SELECT id FROM teams WHERE slug = $1', [slug]);
    if (existing.rows.length > 0) {
      throw new ValidationError('A team with this name already exists');
    }

    // Create team
    const teamResult = await query(`
      INSERT INTO teams (name, slug, description, owner_id, is_public)
      VALUES ($1, $2, $3, $4, $5)
      RETURNING *
    `, [name.trim(), slug, description || null, req.user.id, isPublic || false]);

    const team = teamResult.rows[0];

    // Add creator as owner
    await query(`
      INSERT INTO team_members (team_id, user_id, role)
      VALUES ($1, $2, 'owner')
    `, [team.id, req.user.id]);

    res.status(201).json({
      id: team.team_id,
      name: team.name,
      slug: team.slug,
      description: team.description,
      myRole: 'owner',
      memberCount: 1,
      assetCount: 0,
      createdAt: team.created_at,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /teams/:slug
 * Get team details
 */
router.get('/:slug', optionalAuth, async (req, res, next) => {
  try {
    const { slug } = req.params;

    const teamResult = await query(`
      SELECT t.*, u.username as owner_username
      FROM teams t
      JOIN users u ON t.owner_id = u.id
      WHERE t.slug = $1
    `, [slug]);

    if (teamResult.rows.length === 0) {
      throw new NotFoundError('Team not found');
    }

    const team = teamResult.rows[0];

    // Check access
    let myRole = null;
    if (req.user) {
      const membership = await getTeamMembership(team.id, req.user.id);
      myRole = membership?.role || null;
    }

    if (!team.is_public && !myRole) {
      throw new ForbiddenError('This team is private');
    }

    res.json({
      id: team.team_id,
      name: team.name,
      slug: team.slug,
      description: team.description,
      owner: team.owner_username,
      memberCount: team.member_count,
      assetCount: team.asset_count,
      isPublic: team.is_public,
      inviteOnly: team.invite_only,
      myRole,
      createdAt: team.created_at,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * PUT /teams/:slug
 * Update team settings
 */
router.put('/:slug', authenticate, async (req, res, next) => {
  try {
    const { slug } = req.params;
    const { name, description, isPublic, inviteOnly } = req.body;

    // Get team
    const teamResult = await query('SELECT * FROM teams WHERE slug = $1', [slug]);
    if (teamResult.rows.length === 0) {
      throw new NotFoundError('Team not found');
    }

    const team = teamResult.rows[0];

    // Check permission
    if (!await canManageTeam(team.id, req.user.id)) {
      throw new ForbiddenError('Only team owners and admins can update settings');
    }

    // Build updates
    const updates = [];
    const values = [];
    let paramIndex = 1;

    if (name !== undefined) {
      updates.push(`name = $${paramIndex++}`);
      values.push(name.trim());
    }
    if (description !== undefined) {
      updates.push(`description = $${paramIndex++}`);
      values.push(description);
    }
    if (isPublic !== undefined) {
      updates.push(`is_public = $${paramIndex++}`);
      values.push(isPublic);
    }
    if (inviteOnly !== undefined) {
      updates.push(`invite_only = $${paramIndex++}`);
      values.push(inviteOnly);
    }

    if (updates.length === 0) {
      return res.json({ success: true });
    }

    updates.push(`updated_at = NOW()`);
    values.push(team.id);

    await query(
      `UPDATE teams SET ${updates.join(', ')} WHERE id = $${paramIndex}`,
      values
    );

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

/**
 * DELETE /teams/:slug
 * Delete a team (owner only)
 */
router.delete('/:slug', authenticate, async (req, res, next) => {
  try {
    const { slug } = req.params;

    const teamResult = await query('SELECT * FROM teams WHERE slug = $1', [slug]);
    if (teamResult.rows.length === 0) {
      throw new NotFoundError('Team not found');
    }

    const team = teamResult.rows[0];

    // Only owner can delete
    if (team.owner_id !== req.user.id) {
      throw new ForbiddenError('Only the team owner can delete a team');
    }

    await query('DELETE FROM teams WHERE id = $1', [team.id]);

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

// ============================================
// Team Members
// ============================================

/**
 * GET /teams/:slug/members
 * List team members
 */
router.get('/:slug/members', authenticate, async (req, res, next) => {
  try {
    const { slug } = req.params;

    const teamResult = await query('SELECT * FROM teams WHERE slug = $1', [slug]);
    if (teamResult.rows.length === 0) {
      throw new NotFoundError('Team not found');
    }

    const team = teamResult.rows[0];

    // Check membership
    const membership = await getTeamMembership(team.id, req.user.id);
    if (!membership && !team.is_public) {
      throw new ForbiddenError('You must be a team member to view members');
    }

    const result = await query(`
      SELECT tm.*, u.username, u.display_name, u.avatar_url
      FROM team_members tm
      JOIN users u ON tm.user_id = u.id
      WHERE tm.team_id = $1
      ORDER BY
        CASE tm.role
          WHEN 'owner' THEN 1
          WHEN 'admin' THEN 2
          ELSE 3
        END,
        tm.joined_at
    `, [team.id]);

    res.json({
      members: result.rows.map(m => ({
        userId: m.user_id,
        username: m.username,
        displayName: m.display_name,
        avatarUrl: m.avatar_url,
        role: m.role,
        joinedAt: m.joined_at,
      })),
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /teams/:slug/members
 * Add a member (by username) or accept an invite
 */
router.post('/:slug/members', authenticate, async (req, res, next) => {
  try {
    const { slug } = req.params;
    const { username, inviteCode } = req.body;

    const teamResult = await query('SELECT * FROM teams WHERE slug = $1', [slug]);
    if (teamResult.rows.length === 0) {
      throw new NotFoundError('Team not found');
    }

    const team = teamResult.rows[0];

    // If invite code provided, validate and join
    if (inviteCode) {
      const inviteResult = await query(`
        SELECT * FROM team_invites
        WHERE team_id = $1 AND invite_code = $2 AND expires_at > NOW()
      `, [team.id, inviteCode]);

      if (inviteResult.rows.length === 0) {
        throw new ValidationError('Invalid or expired invite code');
      }

      const invite = inviteResult.rows[0];

      // Check if already a member
      const existing = await getTeamMembership(team.id, req.user.id);
      if (existing) {
        throw new ValidationError('You are already a member of this team');
      }

      // Add member
      await query(`
        INSERT INTO team_members (team_id, user_id, role, invited_by)
        VALUES ($1, $2, $3, $4)
      `, [team.id, req.user.id, invite.role, invite.invited_by]);

      // Update member count
      await query('UPDATE teams SET member_count = member_count + 1 WHERE id = $1', [team.id]);

      // Delete invite
      await query('DELETE FROM team_invites WHERE id = $1', [invite.id]);

      return res.json({ success: true, message: 'Joined team' });
    }

    // Otherwise, admin is adding a user by username
    if (!await canManageTeam(team.id, req.user.id)) {
      throw new ForbiddenError('Only team owners and admins can add members');
    }

    if (!username) {
      throw new ValidationError('Username is required');
    }

    // Find user
    const userResult = await query('SELECT id FROM users WHERE username = $1', [username]);
    if (userResult.rows.length === 0) {
      throw new NotFoundError('User not found');
    }

    const targetUserId = userResult.rows[0].id;

    // Check if already a member
    const existing = await getTeamMembership(team.id, targetUserId);
    if (existing) {
      throw new ValidationError('User is already a member of this team');
    }

    // Add member
    await query(`
      INSERT INTO team_members (team_id, user_id, role, invited_by)
      VALUES ($1, $2, 'member', $3)
    `, [team.id, targetUserId, req.user.id]);

    // Update member count
    await query('UPDATE teams SET member_count = member_count + 1 WHERE id = $1', [team.id]);

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

/**
 * PUT /teams/:slug/members/:username
 * Update member role
 */
router.put('/:slug/members/:username', authenticate, async (req, res, next) => {
  try {
    const { slug, username } = req.params;
    const { role } = req.body;

    if (!['admin', 'member'].includes(role)) {
      throw new ValidationError('Invalid role');
    }

    const teamResult = await query('SELECT * FROM teams WHERE slug = $1', [slug]);
    if (teamResult.rows.length === 0) {
      throw new NotFoundError('Team not found');
    }

    const team = teamResult.rows[0];

    // Only owner can change roles
    const membership = await getTeamMembership(team.id, req.user.id);
    if (!membership || membership.role !== 'owner') {
      throw new ForbiddenError('Only the team owner can change member roles');
    }

    // Find target user
    const userResult = await query('SELECT id FROM users WHERE username = $1', [username]);
    if (userResult.rows.length === 0) {
      throw new NotFoundError('User not found');
    }

    const targetUserId = userResult.rows[0].id;

    // Can't change owner's role
    if (targetUserId === team.owner_id) {
      throw new ForbiddenError('Cannot change the owner\'s role');
    }

    await query(
      'UPDATE team_members SET role = $1 WHERE team_id = $2 AND user_id = $3',
      [role, team.id, targetUserId]
    );

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

/**
 * DELETE /teams/:slug/members/:username
 * Remove a member or leave team
 */
router.delete('/:slug/members/:username', authenticate, async (req, res, next) => {
  try {
    const { slug, username } = req.params;

    const teamResult = await query('SELECT * FROM teams WHERE slug = $1', [slug]);
    if (teamResult.rows.length === 0) {
      throw new NotFoundError('Team not found');
    }

    const team = teamResult.rows[0];

    // Find target user
    const userResult = await query('SELECT id FROM users WHERE username = $1', [username]);
    if (userResult.rows.length === 0) {
      throw new NotFoundError('User not found');
    }

    const targetUserId = userResult.rows[0].id;
    const isSelf = targetUserId === req.user.id;

    // Owner can't leave (must delete team or transfer)
    if (targetUserId === team.owner_id) {
      throw new ForbiddenError('Team owner cannot leave. Delete the team or transfer ownership first.');
    }

    // Check permission
    if (!isSelf && !await canManageTeam(team.id, req.user.id)) {
      throw new ForbiddenError('Only team owners and admins can remove members');
    }

    await query(
      'DELETE FROM team_members WHERE team_id = $1 AND user_id = $2',
      [team.id, targetUserId]
    );

    // Update member count
    await query('UPDATE teams SET member_count = member_count - 1 WHERE id = $1', [team.id]);

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

// ============================================
// Team Saved Assets (Team Library)
// ============================================

/**
 * GET /teams/:slug/saved
 * Get team's saved assets (their shared library)
 */
router.get('/:slug/saved', authenticate, async (req, res, next) => {
  try {
    const { slug } = req.params;
    const { folder, limit = 100, offset = 0 } = req.query;

    const teamResult = await query('SELECT * FROM teams WHERE slug = $1', [slug]);
    if (teamResult.rows.length === 0) {
      throw new NotFoundError('Team not found');
    }

    const team = teamResult.rows[0];

    // Check membership
    const membership = await getTeamMembership(team.id, req.user.id);
    if (!membership) {
      throw new ForbiddenError('You must be a team member to view the library');
    }

    // Build query
    let sql = `
      SELECT
        tsa.*,
        a.asset_id, a.name, a.slug as asset_slug, a.asset_type, a.houdini_context,
        a.description, a.tags, a.latest_version, a.download_count,
        u.username as owner_username,
        v.thumbnail_url,
        adder.username as added_by_username
      FROM team_saved_assets tsa
      JOIN assets a ON tsa.asset_id = a.id
      JOIN users u ON a.owner_id = u.id
      LEFT JOIN versions v ON a.latest_version_id = v.id
      LEFT JOIN users adder ON tsa.added_by = adder.id
      WHERE tsa.team_id = $1
    `;
    const params = [team.id];
    let paramIndex = 2;

    if (folder) {
      sql += ` AND tsa.folder = $${paramIndex++}`;
      params.push(folder);
    }

    sql += ` ORDER BY tsa.added_at DESC LIMIT $${paramIndex++} OFFSET $${paramIndex++}`;
    params.push(parseInt(limit), parseInt(offset));

    const result = await query(sql, params);

    res.json({
      assets: result.rows.map(a => ({
        id: a.asset_id,
        name: a.name,
        slug: `${a.owner_username}/${a.asset_slug}`,
        type: a.asset_type,
        context: a.houdini_context,
        description: a.description,
        tags: a.tags,
        latestVersion: a.latest_version,
        thumbnailUrl: a.thumbnail_url,
        downloadCount: a.download_count,
        // Team-specific
        addedAt: a.added_at,
        addedBy: a.added_by_username,
        folder: a.folder,
        notes: a.notes,
      })),
      teamSlug: team.slug,
      teamName: team.name,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /teams/:slug/saved/:assetSlug
 * Add an asset to team's library
 */
router.post('/:slug/saved/:ownerSlug/:assetSlug', authenticate, async (req, res, next) => {
  try {
    const { slug, ownerSlug, assetSlug } = req.params;
    const { folder, notes } = req.body;

    const teamResult = await query('SELECT * FROM teams WHERE slug = $1', [slug]);
    if (teamResult.rows.length === 0) {
      throw new NotFoundError('Team not found');
    }

    const team = teamResult.rows[0];

    // Check membership (any member can add)
    const membership = await getTeamMembership(team.id, req.user.id);
    if (!membership) {
      throw new ForbiddenError('You must be a team member to add assets');
    }

    // Find asset
    const assetResult = await query(`
      SELECT a.*, v.id as latest_vid
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      LEFT JOIN versions v ON a.latest_version_id = v.id
      WHERE u.username = $1 AND a.slug = $2 AND a.is_public = true
    `, [ownerSlug, assetSlug]);

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const asset = assetResult.rows[0];

    // Add to team library
    await query(`
      INSERT INTO team_saved_assets (team_id, asset_id, version_id, added_by, folder, notes)
      VALUES ($1, $2, $3, $4, $5, $6)
      ON CONFLICT (team_id, asset_id) DO UPDATE SET
        folder = COALESCE(EXCLUDED.folder, team_saved_assets.folder),
        notes = COALESCE(EXCLUDED.notes, team_saved_assets.notes)
    `, [team.id, asset.id, asset.latest_vid, req.user.id, folder || null, notes || null]);

    // Update team asset count
    await query(`
      UPDATE teams SET asset_count = (
        SELECT COUNT(*) FROM team_saved_assets WHERE team_id = $1
      ) WHERE id = $1
    `, [team.id]);

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

/**
 * DELETE /teams/:slug/saved/:ownerSlug/:assetSlug
 * Remove an asset from team's library
 */
router.delete('/:slug/saved/:ownerSlug/:assetSlug', authenticate, async (req, res, next) => {
  try {
    const { slug, ownerSlug, assetSlug } = req.params;

    const teamResult = await query('SELECT * FROM teams WHERE slug = $1', [slug]);
    if (teamResult.rows.length === 0) {
      throw new NotFoundError('Team not found');
    }

    const team = teamResult.rows[0];

    // Check permission (admins/owners can remove)
    if (!await canManageTeam(team.id, req.user.id)) {
      throw new ForbiddenError('Only team owners and admins can remove assets');
    }

    // Find asset
    const assetResult = await query(`
      SELECT a.id FROM assets a
      JOIN users u ON a.owner_id = u.id
      WHERE u.username = $1 AND a.slug = $2
    `, [ownerSlug, assetSlug]);

    if (assetResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    await query(
      'DELETE FROM team_saved_assets WHERE team_id = $1 AND asset_id = $2',
      [team.id, assetResult.rows[0].id]
    );

    // Update team asset count
    await query(`
      UPDATE teams SET asset_count = (
        SELECT COUNT(*) FROM team_saved_assets WHERE team_id = $1
      ) WHERE id = $1
    `, [team.id]);

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

// ============================================
// Team Invites
// ============================================

/**
 * POST /teams/:slug/invites
 * Create an invite link
 */
router.post('/:slug/invites', authenticate, async (req, res, next) => {
  try {
    const { slug } = req.params;
    const { email, role = 'member', expiresInDays = 7 } = req.body;

    const teamResult = await query('SELECT * FROM teams WHERE slug = $1', [slug]);
    if (teamResult.rows.length === 0) {
      throw new NotFoundError('Team not found');
    }

    const team = teamResult.rows[0];

    if (!await canManageTeam(team.id, req.user.id)) {
      throw new ForbiddenError('Only team owners and admins can create invites');
    }

    const inviteCode = crypto.randomBytes(32).toString('hex');
    const expiresAt = new Date(Date.now() + expiresInDays * 24 * 60 * 60 * 1000);

    await query(`
      INSERT INTO team_invites (team_id, email, invite_code, role, invited_by, expires_at)
      VALUES ($1, $2, $3, $4, $5, $6)
      ON CONFLICT (team_id, email) DO UPDATE SET
        invite_code = EXCLUDED.invite_code,
        expires_at = EXCLUDED.expires_at
    `, [team.id, email || '', inviteCode, role, req.user.id, expiresAt]);

    const webUrl = process.env.WEB_URL || 'http://localhost:5173';
    const inviteUrl = `${webUrl}/teams/${slug}/join?code=${inviteCode}`;

    res.json({
      inviteCode,
      inviteUrl,
      expiresAt: expiresAt.toISOString(),
    });
  } catch (error) {
    next(error);
  }
});

export default router;
