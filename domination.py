# Released under the MIT License. See LICENSE for details.
#
"""Defines the King of the Hill game."""

# ba_meta require api 8
# (see https://ballistica.net/wiki/meta-tag-system)
# MADE BY CORPSE; Discord ID: imcorpsed

from __future__ import annotations

import weakref, random
from enum import Enum
from typing import TYPE_CHECKING, override

import bascenev1 as bs

from bascenev1lib.actor.flag import Flag
from bascenev1lib.actor.playerspaz import PlayerSpaz
from bascenev1lib.actor.scoreboard import Scoreboard
from bascenev1lib.gameutils import SharedObjects
from bascenev1lib.actor.popuptext import PopupText

if TYPE_CHECKING:
    from typing import Any, Sequence

flag_positions = {
    "Flag A": (-6.284710884094238, 4.630422592163086, -8.474217414855957),
    "Flag C": (6.885792232387695, 4.6298604011535645, -8.584202766418457)
}

class FlagState(Enum):
    """States our single flag can be in."""

    NEW = 0
    UNCONTESTED = 1
    CONTESTED = 2
    HELD = 3


class Player(bs.Player['Team']):
    """Our player type for this game."""

    def __init__(self) -> None:
        self.time_at_flag_a = 0
        self.time_at_flag_b = 0
        self.time_at_flag_c = 0


class Team(bs.Team[Player]):
    """Our team type for this game."""

    def __init__(self, time_remaining: int) -> None:
        self.time_remaining = time_remaining
        self.a_standing_since = 0
        self.score = 0
        self.b_standing_since = 0
        self.c_standing_since = 0


# ba_meta export bascenev1.GameActivity
class KingOfTheHillGame(bs.TeamGameActivity[Player, Team]):
    """Game where a team wins by holding a 'hill' for a set amount of time."""

    name = 'Domination'
    description = 'Secure the regions for a set amount of points.'
    available_settings = [
        bs.IntSetting(
            'Points To Win',
            min_value=60,
            default=30,
            increment=10,
        ),
        bs.IntChoiceSetting(
            'Time Limit',
            choices=[
                ('None', 0),
                ('1 Minute', 60),
                ('2 Minutes', 120),
                ('5 Minutes', 300),
                ('10 Minutes', 600),
                ('20 Minutes', 1200),
            ],
            default=0,
        ),
        bs.FloatChoiceSetting(
            'Respawn Times',
            choices=[
                ('Shorter', 0.25),
                ('Short', 0.5),
                ('Normal', 1.0),
                ('Long', 2.0),
                ('Longer', 4.0),
            ],
            default=1.0,
        ),
        bs.BoolSetting('Epic Mode', default=False),
    ]
    scoreconfig = bs.ScoreConfig(label='Time Held')

    @override
    @classmethod
    def supports_session_type(cls, sessiontype: type[bs.Session]) -> bool:
        return issubclass(sessiontype, bs.MultiTeamSession)

    @override
    @classmethod
    def get_supported_maps(cls, sessiontype: type[bs.Session]) -> list[str]:
        assert bs.app.classic is not None
        return bs.app.classic.getmaps('king_of_the_hill')

    def __init__(self, settings: dict):
        super().__init__(settings)
        shared = SharedObjects.get()
        self._scoreboard = Scoreboard()
        self._score_to_win = int(settings['Points To Win'])
        self._swipsound = bs.getsound('swip')
        self._tick_sound = bs.getsound('tick')
        self._countdownsounds = {
            10: bs.getsound('announceTen'),
            9: bs.getsound('announceNine'),
            8: bs.getsound('announceEight'),
            7: bs.getsound('announceSeven'),
            6: bs.getsound('announceSix'),
            5: bs.getsound('announceFive'),
            4: bs.getsound('announceFour'),
            3: bs.getsound('announceThree'),
            2: bs.getsound('announceTwo'),
            1: bs.getsound('announceOne'),
        }
        self._standing_a = []
        self._standing_b = []
        self._standing_c = []
        self.captures = {}
        self._flag_b_pos: Sequence[float] | None = None
        self._flag_b_state: FlagState | None = None
        self._flag: Flag | None = None
        self._flag_b_light: bs.Node | None = None
        self._scoring_team: weakref.ref[Team] | None = None
        self._hold_time = int(settings['Hold Time'])
        self._time_limit = float(settings['Time Limit'])
        self._epic_mode = bool(settings['Epic Mode'])
        self._flag_b_region_material = bs.Material()
        self._flag_b_region_material.add_actions(
            conditions=('they_have_material', shared.player_material),
            actions=(
                ('modify_part_collision', 'collide', True),
                ('modify_part_collision', 'physical', False),
                (
                    'call',
                    'at_connect',
                    bs.Call(self._handle_player_flag_b_region_collide, True),
                ),
                (
                    'call',
                    'at_disconnect',
                    bs.Call(self._handle_player_flag_b_region_collide, False),
                ),
            ),
        )
        self._flag_a_region_material = bs.Material()
        self._flag_a_region_material.add_actions(
            conditions=('they_have_material', shared.player_material),
            actions=(
                ('modify_part_collision', 'collide', True),
                ('modify_part_collision', 'physical', False),
                (
                    'call',
                    'at_connect',
                    bs.Call(self._handle_player_flag_a_region_collide, True),
                ),
                (
                    'call',
                    'at_disconnect',
                    bs.Call(self._handle_player_flag_a_region_collide, False),
                ),
            ),
        )
        self._flag_c_region_material = bs.Material()
        self._flag_c_region_material.add_actions(
            conditions=('they_have_material', shared.player_material),
            actions=(
                ('modify_part_collision', 'collide', True),
                ('modify_part_collision', 'physical', False),
                (
                    'call',
                    'at_connect',
                    bs.Call(self._handle_player_flag_c_region_collide, True),
                ),
                (
                    'call',
                    'at_disconnect',
                    bs.Call(self._handle_player_flag_c_region_collide, False),
                ),
            ),
        )

        # Base class overrides.
        self.slow_motion = self._epic_mode
        self.default_music = (
            bs.MusicType.EPIC if self._epic_mode else bs.MusicType.SCARY
        )

    @override
    def get_instance_description(self) -> str | Sequence:
        return 'Secure the regions for 150 points.', self._score_to_win

    @override
    def get_instance_description_short(self) -> str | Sequence:
        return 'secure the regions for ${ARG1} points', self._score_to_win

    @override
    def create_team(self, sessionteam: bs.SessionTeam) -> Team:
        return Team(time_remaining=self._hold_time)

    @override
    def on_begin(self) -> None:
        super().on_begin()
        shared = SharedObjects.get()
        self.setup_standard_time_limit(self._time_limit)
        self.setup_standard_powerup_drops()
        # Flag A
        self._flag_a_pos = self.map.flag_points[0]
        self._flag_a_state = FlagState.NEW
        Flag.project_stand(self._flag_a_pos)
        self._flag_a = Flag(
            position=self._flag_a_pos, touchable=False, color=(1, 1, 1)
        )
        self.captures[self._flag_a] = None
        self.a_tnode = bs.newnode(
                        'text',
                        attrs={
                            'text': "Uncaptured",
                            'in_world': True,
                            'scale': 0.013,
                            'color': (1, 1, 0, 1),
                            'h_align': 'center',
                            'position': (self._flag_a_pos[0], self._flag_a_pos[1]+1.2, self._flag_a_pos[2]),
                        },
                    )
        self._flag_a_light = bs.newnode(
            'light',
            attrs={
                'position': self._flag_a_pos,
                'intensity': 0.2,
                'height_attenuated': False,
                'radius': 0.4,
                'color': (0.2, 0.2, 0.2),
            },
        )
        # Flag A region.
        flagmats = [self._flag_a_region_material, shared.region_material]
        bs.newnode(
            'region',
            attrs={
                'position': self._flag_a_pos,
                'scale': (1.8, 1.8, 1.8),
                'type': 'sphere',
                'materials': flagmats,
            },
        )

        # Flag B
        self._flag_b_pos = self.map.get_flag_position(None)
        self.b_tnode = bs.newnode(
                        'text',
                        attrs={
                            'text': "Uncaptured",
                            'in_world': True,
                            'scale': 0.013,
                            'color': (1, 1, 0, 1),
                            'h_align': 'center',
                            'position': (self._flag_b_pos[0], self._flag_b_pos[1]+1.2, self._flag_b_pos[2]),
                        },
                    )
        self._flag_b_state = FlagState.NEW
        Flag.project_stand(self._flag_b_pos)
        self._flag_b = Flag(
            position=self._flag_b_pos, touchable=False, color=(1, 1, 1)
        )
        self.captures[self._flag_b] = None
        self._flag_b_light = bs.newnode(
            'light',
            attrs={
                'position': self._flag_b_pos,
                'intensity': 0.2,
                'height_attenuated': False,
                'radius': 0.4,
                'color': (0.2, 0.2, 0.2),
            },
        )
        # Flag B region.
        flagmats = [self._flag_b_region_material, shared.region_material]
        bs.newnode(
            'region',
            attrs={
                'position': self._flag_b_pos,
                'scale': (1.8, 1.8, 1.8),
                'type': 'sphere',
                'materials': flagmats,
            },
        )

        # Flag C
        self._flag_c_pos = self.map.flag_points[1]
        self.c_tnode = bs.newnode(
                        'text',
                        attrs={
                            'text': "Uncaptured",
                            'in_world': True,
                            'scale': 0.013,
                            'color': (1, 1, 0, 1),
                            'h_align': 'center',
                            'position': (self._flag_c_pos[0], self._flag_c_pos[1]+1.2, self._flag_c_pos[2]),
                        },
                    )
        self._flag_c_state = FlagState.NEW
        Flag.project_stand(self._flag_c_pos)
        self._flag_c = Flag(
            position=self._flag_c_pos, touchable=False, color=(1, 1, 1)
        )
        self.captures[self._flag_c] = None
        self._flag_c_light = bs.newnode(
            'light',
            attrs={
                'position': self._flag_c_pos,
                'intensity': 0.2,
                'height_attenuated': False,
                'radius': 0.4,
                'color': (0.2, 0.2, 0.2),
            },
        )
        # Flag C region.
        flagmats = [self._flag_c_region_material, shared.region_material]
        bs.newnode(
            'region',
            attrs={
                'position': self._flag_c_pos,
                'scale': (1.8, 1.8, 1.8),
                'type': 'sphere',
                'materials': flagmats,
            },
        )

        self._update_scoreboard()
        self.ticktim = bs.timer(1.0, self._tick, repeat=True)
        self.tickatim = bs.timer(1.0, self._ticka, repeat=True)
        self.tickbtim = bs.timer(1.0, self._tickb, repeat=True)
        self.tickctim = bs.timer(1.0, self._tickc, repeat=True)
        self._update_flag_a_state()
        self._update_flag_b_state()
        self._update_flag_c_state()

    def _tick(self) -> None:
        for i in self.teams:
            if self.captures[self._flag_a] == i:
                i.score += 1
                req = f"Captured by {i.name.evaluate()}"
                if not self.a_tnode.text == req:
                    self.a_tnode.text = req
                    self._flag_a.color = i[0].color
                    i[0].a_standing_since = 0
                self._update_scoreboard()
            if self.captures[self._flag_b] == i:
                i.score += 3
                req = f"Captured by {i.name.evaluate()}"
                if not self.b_tnode.text == req:
                    self.b_tnode.text = req
                    self._flag_b.color = i[0].color
                    i[0].b_standing_since = 0
                self._update_scoreboard()
            if self.captures[self._flag_c] == i:
                i.score += 1
                req = f"Captured by {i.name.evaluate()}"
                if not self.c_tnode.text == req:
                    self.c_tnode.text = req
                    self._flag_c.color = i[0].color
                    i[0].c_standing_since = 0
                self._update_scoreboard()
        if any(team.score >= self._score_to_win for team in self.teams):
            self.ticktim = None
            self.tickatim = None
            self.tickbtim = None
            self.tickctim = None
            bs.timer(0.5, self.end_game)

    def _ticka(self) -> None:
        if self._standing_a:
            teams = list(set([x.team for x in self._standing_a]))
            if len(teams) > 1:
                self.a_tnode.text = "CONQUESTED"
                self.captures[self._flag_a] = None
                self.a_tnode.color = (1,0,0)
            elif len(teams) == 1:
                if teams[0] == self.captures[self._flag_a]:
                    return
                if not teams[0] == self.captures[self._flag_a]: 
                    self.a_tnode.text = f"{teams[0].name.evaluate()} Capturing..."
                    self.a_tnode.color = teams[0].color
                    teams[0].a_standing_since += 1
                    if teams[0].a_standing_since == 3:
                        self.captures[self._flag_a] = teams[0]
                        self.a_tnode.text = f"Captured by {teams[0].name.evaluate()}"
                        self._flag_a.color = teams[0].color
                        teams[0].a_standing_since = 0
                    
            
    def _tickb(self) -> None:
        if self._standing_b:
            teams = list(set([x.team for x in self._standing_b]))
            if len(teams) > 1:
                self.b_tnode.text = "CONQUESTED"
                self.captures[self._flag_b] = None
                self.b_tnode.color = (1,0,0)
            elif len(teams) == 1:
                if teams[0] == self.captures[self._flag_b]:
                    return
                if not teams[0] == self.captures[self._flag_b]: 
                    self.b_tnode.text = f"{teams[0].name.evaluate()} Capturing..."
                    self.b_tnode.color = teams[0].color
                    teams[0].b_standing_since += 1
                    if teams[0].b_standing_since == 3:
                        self.captures[self._flag_b] = teams[0]
                        self.b_tnode.text = f"Captured by {teams[0].name.evaluate()}"
                        self._flag_b.color = teams[0].color
                        teams[0].b_standing_since = 0
            
    def _tickc(self) -> None:
        if self._standing_c:
            teams = list(set([x.team for x in self._standing_c]))
            if len(teams) > 1:
                self.c_tnode.text = "CONQUESTED"
                self.captures[self._flag_c] = None
                self.c_tnode.color = (1,0,0)
            elif len(teams) == 1:
                if teams[0] == self.captures[self._flag_c]:
                    return
                if not teams[0] == self.captures[self._flag_c]: 
                    self.c_tnode.text = f"{teams[0].name.evaluate()} Capturing..."
                    self.c_tnode.color = teams[0].color
                    teams[0].c_standing_since += 1
                    if teams[0].c_standing_since == 3:
                        self.captures[self._flag_c] = teams[0]
                        self.c_tnode.text = f"Captured by {teams[0].name.evaluate()}"
                        self._flag_c.color = teams[0].color
                        teams[0].c_standing_since = 0
            

            
    @override
    def end_game(self) -> None:
        results = bs.GameResults()
        for team in self.teams:
            if any(team.score >= self._score_to_win for team in self.teams):
                bs.timer(0.5, self.end_game)
        self.end(results=results, announce_delay=0)

    def _update_flag_b_state(self) -> None:
        holding_teams = set(
            player.team for player in self.players if player.time_at_flag_b
        )
        prev_state = self._flag_b_state
        assert self._flag_b_light
        assert self._flag is not None
        assert self._flag_b.node
        if len(holding_teams) > 1:
            self._flag_b_state = FlagState.CONTESTED
            self._scoring_team = None
            self._flag_b_light.color = (0.6, 0.6, 0.1)
            self._flag_b.node.color = (1.0, 1.0, 0.4)
        elif len(holding_teams) == 1:
            holding_team = list(holding_teams)[0]
            self._flag_b_state = FlagState.HELD
            self._scoring_team = weakref.ref(holding_team)
            self._flag_b_light.color = bs.normalized_color(holding_team.color)
            self._flag_b.node.color = holding_team.color
        else:
            self._flag_b_state = FlagState.UNCONTESTED
            self._scoring_team = None
            self._flag_b_light.color = (0.2, 0.2, 0.2)
            self._flag_b.node.color = (1, 1, 1)
        if self._flag_b_state != prev_state:
            self._swipsound.play()

    def _update_flag_a_state(self) -> None:
        holding_teams = set(
            player.team for player in self.players if player.time_at_flag_a
        )
        prev_state = self._flag_a_state
        assert self._flag_a_light
        assert self._flag is not None
        assert self._flag_a.node
        if len(holding_teams) > 1:
            self._flag_a_state = FlagState.CONTESTED
            self._scoring_team = None
            self._flag_a_light.color = (0.6, 0.6, 0.1)
            self._flag_a.node.color = (1.0, 1.0, 0.4)
        elif len(holding_teams) == 1:
            holding_team = list(holding_teams)[0]
            self._flag_a_state = FlagState.HELD
            self._scoring_team = weakref.ref(holding_team)
            self._flag_a_light.color = bs.normalized_color(holding_team.color)
            self._flag_a.node.color = holding_team.color
        else:
            self._flag_a_state = FlagState.UNCONTESTED
            self._scoring_team = None
            self._flag_a_light.color = (0.2, 0.2, 0.2)
            self._flag_a.node.color = (1, 1, 1)
        if self._flag_a_state != prev_state:
            self._swipsound.play()

    def _update_flag_c_state(self) -> None:
        holding_teams = set(
            player.team for player in self.players if player.time_at_flag_c
        )
        prev_state = self._flag_c_state
        assert self._flag_c_light
        assert self._flag is not None
        assert self._flag_c.node
        if len(holding_teams) > 1:
            self._flag_c_state = FlagState.CONTESTED
            self._scoring_team = None
            self._flag_c_light.color = (0.6, 0.6, 0.1)
            self._flag_c.node.color = (1.0, 1.0, 0.4)
        elif len(holding_teams) == 1:
            holding_team = list(holding_teams)[0]
            self._flag_c_state = FlagState.HELD
            self._scoring_team = weakref.ref(holding_team)
            self._flag_c_light.color = bs.normalized_color(holding_team.color)
            self._flag_c.node.color = holding_team.color
        else:
            self._flag_c_state = FlagState.UNCONTESTED
            self._scoring_team = None
            self._flag_c_light.color = (0.2, 0.2, 0.2)
            self._flag_c.node.color = (1, 1, 1)
        if self._flag_c_state != prev_state:
            self._swipsound.play()

    def _handle_player_flag_a_region_collide(self, colliding: bool) -> None:
        try:
            spaz = bs.getcollision().opposingnode.getdelegate(PlayerSpaz, True)
        except bs.NotFoundError:
            return

        if not spaz.is_alive():
            return

        player = spaz.getplayer(Player, True)

        # Different parts of us can collide so a single value isn't enough
        # also don't count it if we're dead (flying heads shouldn't be able to
        # win the game :-)
        if colliding and player.is_alive():
            player.time_at_flag_a += 1
        else:
            player.time_at_flag_a = max(0, player.time_at_flag_a - 1)
        if colliding:
            if not player in self._standing_a:
                self._standing_a.append(player)
        else:
            if player in self._standing_a:
                self._standing_a.remove(player)
        if len(self._standing_a) == 0 and self.captures[self._flag_a] is None:
            self.a_tnode.text = f"Uncaptured"
            self._flag_a.color = (1,1,1)
            self.a_tnode.color = (1,1,0,1)
        self._update_flag_a_state()

    def _handle_player_flag_b_region_collide(self, colliding: bool) -> None:
        try:
            spaz = bs.getcollision().opposingnode.getdelegate(PlayerSpaz, True)
        except bs.NotFoundError:
            return

        if not spaz.is_alive():
            return

        player = spaz.getplayer(Player, True)

        # Different parts of us can collide so a single value isn't enough
        # also don't count it if we're dead (flying heads shouldn't be able to
        # win the game :-)
        if colliding and player.is_alive():
            player.time_at_flag_b += 1
        else:
            player.time_at_flag_b = max(0, player.time_at_flag_b - 1)

        if colliding:
            if not player in self._standing_b:
                self._standing_b.append(player)
        else:
            if player in self._standing_b:
                self._standing_b.remove(player)
        if len(self._standing_b) == 0 and self.captures[self._flag_a] is None:
            self.b_tnode.text = f"Uncaptured"
            self._flag_a.color = (1,1,1)
            self.b_tnode.color = (1,1,0,1)
        self._update_flag_b_state()

    def _handle_player_flag_c_region_collide(self, colliding: bool) -> None:
        try:
            spaz = bs.getcollision().opposingnode.getdelegate(PlayerSpaz, True)
        except bs.NotFoundError:
            return

        if not spaz.is_alive():
            return

        player = spaz.getplayer(Player, True)

        # Different parts of us can collide so a single value isn't enough
        # also don't count it if we're dead (flying heads shouldn't be able to
        # win the game :-)
        if colliding and player.is_alive():
            player.time_at_flag_c += 1
        else:
            player.time_at_flag_c = max(0, player.time_at_flag_c - 1)

        if colliding:
            if not player in self._standing_c:
                self._standing_c.append(player)
        else:
            if player in self._standing_c:
                self._standing_c.remove(player)
        if len(self._standing_c) == 0 and self.captures[self._flag_a] is None:
            self.c_tnode.text = f"Uncaptured"
            self.c_tnode.color = (1,1,0,1)
            self._flag_a.color = (1,1,1)
        self._update_flag_c_state()

    def _update_scoreboard(self) -> None:
        for team in self.teams:
            self._scoreboard.set_team_value(
                team, team.score, self._score_to_win
            )

    @override
    def handlemessage(self, msg: Any) -> Any:
        if isinstance(msg, bs.PlayerDiedMessage):
            super().handlemessage(msg)  # Augment default.

            # No longer can count as time_at_flag once dead.
            player = msg.getplayer(Player)
            player.time_at_flag_a = 0
            player.time_at_flag_b = 0
            player.time_at_flag_c = 0
            self._update_flag_a_state()
            self._update_flag_b_state()
            self._update_flag_c_state()
            self.respawn_player(player)
