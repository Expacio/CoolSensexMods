# Released under the MIT License. See LICENSE for details.
#
"""DeathMatch game and support classes."""

# ba_meta require api 9
# (see https://ballistica.net/wiki/meta-tag-system)

from __future__ import annotations

from typing import TYPE_CHECKING

from bascenev1lib.actor.playerspaz import PlayerSpaz
from bascenev1lib.actor.popuptext import PopupText
from bascenev1lib.actor.scoreboard import Scoreboard
from bascenev1lib.gameutils import SharedObjects
import random
import bascenev1 as bs

if TYPE_CHECKING:
    from typing import Any, Sequence


class Player(bs.Player['Team']):
    """Our player type for this game."""


class Team(bs.Team[Player]):
    """Our team type for this game."""

    def __init__(self) -> None:
        self.score = 0

class Egg(bs.Actor):
    """A lovely egg that can be picked up for points."""

    def __init__(self, position: tuple[float, float, float] = (0.0, 1.0, 0.0)):
        super().__init__()
        activity = self.activity
        assert isinstance(activity, KillConfirmedGame)
        shared = SharedObjects.get()

        # Spawn just above the provided point.
        self._spawn_pos = (position[0], position[1] + 1.0, position[2])
        ctex = (activity.egg_tex_1, activity.egg_tex_2, activity.egg_tex_3)[
            random.randrange(3)
        ]
        mats = [shared.object_material, activity.egg_material]
        self.node = bs.newnode(
            'prop',
            delegate=self,
            attrs={
                'mesh': activity.egg_mesh,
                'color_texture': ctex,
                'body': 'capsule',
                'reflection': 'soft',
                'mesh_scale': 0.5,
                'body_scale': 0.5,
                'density': 4.0,
                'reflection_scale': [0.15],
                'shadow_size': 0.6,
                'position': self._spawn_pos,
                'materials': mats,
            },
        )

    def exists(self) -> bool:
        return bool(self.node)

    def handlemessage(self, msg: Any) -> Any:
        if isinstance(msg, bs.DieMessage):
            if self.node:
                self.node.delete()
                self.lightnode.delete()
        elif isinstance(msg, bs.HitMessage):
            if self.node:
                assert msg.force_direction is not None
                self.node.handlemessage(
                    'impulse',
                    msg.pos[0],
                    msg.pos[1],
                    msg.pos[2],
                    msg.velocity[0],
                    msg.velocity[1],
                    msg.velocity[2],
                    1.0 * msg.magnitude,
                    1.0 * msg.velocity_magnitude,
                    msg.radius,
                    0,
                    msg.force_direction[0],
                    msg.force_direction[1],
                    msg.force_direction[2],
                )
        else:
            super().handlemessage(msg)


# ba_meta export bascenev1.GameActivity
class KillConfirmedGame(bs.TeamGameActivity[Player, Team]):
    """A game type based on acquiring kills."""

    name = 'Kill Confirmed'
    description = 'Kill a set number of enemies, collect their eggs to score.'

    # Print messages when players die since it matters here.
    announce_player_deaths = True

    @classmethod
    def get_available_settings(
        cls, sessiontype: type[bs.Session]
    ) -> list[bs.Setting]:
        settings = [
            bs.IntSetting(
                'Egg claims to Win Per Player',
                min_value=1,
                default=5,
                increment=1,
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

        # In teams mode, a suicide gives a point to the other team, but in
        # free-for-all it subtracts from your own score. By default we clamp
        # this at zero to benefit new players, but pro players might like to
        # be able to go negative. (to avoid a strategy of just
        # suiciding until you get a good drop)
        if issubclass(sessiontype, bs.FreeForAllSession):
            settings.append(
                bs.BoolSetting('Allow Negative Scores', default=False)
            )

        return settings

    @classmethod
    def supports_session_type(cls, sessiontype: type[bs.Session]) -> bool:
        return issubclass(sessiontype, bs.DualTeamSession) or issubclass(
            sessiontype, bs.FreeForAllSession
        )

    @classmethod
    def get_supported_maps(cls, sessiontype: type[bs.Session]) -> list[str]:
        assert bs.app.classic is not None
        return bs.app.classic.getmaps('melee')

    def __init__(self, settings: dict):
        super().__init__(settings)
        self._scoreboard = Scoreboard()
        self._score_to_win: int | None = None
        self._tags = {}
        self._dingsound = bs.getsound('dingSmall')
        self.egg_tex_1 = bs.gettexture('eggTex1')
        shared = SharedObjects.get()
        self.egg_mesh = bs.getmesh('egg')
        self.egg_tex_2 = bs.gettexture('eggTex2')
        self.egg_tex_3 = bs.gettexture('eggTex3')
        self._collect_sound = bs.getsound('powerup01')
        self._max_eggs = 1.0
        self.egg_material = bs.Material()
        self.egg_material.add_actions(
            conditions=('they_have_material', shared.player_material),
            actions=(('call', 'at_connect', self._on_egg_player_collide),),
        )
        self._epic_mode = bool(settings['Epic Mode'])
        self._kills_to_win_per_player = int(settings['Egg claims to Win Per Player'])
        self._time_limit = float(settings['Time Limit'])
        self._allow_negative_scores = bool(
            settings.get('Allow Negative Scores', False)
        )

        # Base class overrides.
        self.slow_motion = self._epic_mode
        self.default_music = (
            bs.MusicType.EPIC if self._epic_mode else bs.MusicType.TO_THE_DEATH
        )

    def _on_egg_player_collide(self) -> None:
        try:
            if self.has_ended():
                return
            collision = bs.getcollision()

            # Be defensive here; we could be hitting the corpse of a player
            # who just left/etc.
            try:
                egg = collision.sourcenode.getdelegate(Egg, True)
                player = collision.opposingnode.getdelegate(
                    PlayerSpaz, True
                ).getplayer(Player, True)
            except bs.NotFoundError:
                return
            egg_owner = self._tags[egg]
            egg.lightnode.delete()
            egg.handlemessage(bs.DieMessage())
            if egg_owner.team is not player.team:
                player.team.score += 1
            del self._tags[egg]
            PopupText(f"{player.team.score}/{self._score_to_win} eggs collected!" if egg_owner.team is not player.team else f"Teammate's egg rescued",
                color=(egg_owner.team.color),
                scale=1.5,
                position=player.actor.node.position,
            ).autoretain()
            assert self._score_to_win is not None
            if any(team.score >= self._score_to_win for team in self.teams):
                bs.timer(0.5, self.end_game)
            self._update_scoreboard()
        except:
            pass

    def get_instance_description(self) -> str | Sequence:
        return 'Crush & collect eggs of ${ARG1} enemies.', self._score_to_win

    def get_instance_description_short(self) -> str | Sequence:
        return 'kill & collect eggs of ${ARG1} enemies', self._score_to_win

    def on_team_join(self, team: Team) -> None:
        if self.has_begun():
            self._update_scoreboard()

    def on_begin(self) -> None:
        super().on_begin()
        bs.broadcastmessage("Kill enemies, collect the eggs that they drop to score\nRescue eggs dropped by teammates!", color = (0,1,0))
        self.setup_standard_time_limit(self._time_limit)
        self.setup_standard_powerup_drops()

        # Base kills needed to win on the size of the largest team.
        self._score_to_win = self._kills_to_win_per_player
        self._update_scoreboard()

    def handlemessage(self, msg: Any) -> Any:
        if isinstance(msg, bs.PlayerDiedMessage):
            # Augment standard behavior.
            super().handlemessage(msg)

            player = msg.getplayer(Player)
            self.respawn_player(player)

            killer = msg.getkillerplayer(Player)
            if killer is None:
                return None

            the_egg = Egg(position=(player.actor.node.position[0]+1, player.actor.node.position[1]+3, player.actor.node.position[2]+1))
            self._tags[the_egg] = player
            txtnode = bs.newnode(
                                'text',
                                owner=the_egg.node,
                                attrs={
                                    'text': f"{player.getname()}",
                                    'color': player.team.color,
                                    'scale': 0.010,
                                    'in_world': True
                                },
                            )
            light = bs.newnode(
            'light',
            attrs={
                'position': the_egg.node.position,
                'height_attenuated': False,
                'radius': 0.1,
                'color': player.team.color,
            },
        )
            the_egg.node.connectattr('position', txtnode, 'position')
            the_egg.node.connectattr('position', light, 'position')
            the_egg.lightnode = light


            # If someone has won, set a timer to end shortly.
            # (allows the dust to clear and draws to occur if deaths are
            # close enough)


        else:
            return super().handlemessage(msg)
        return None

    def _update_scoreboard(self) -> None:
        for team in self.teams:
            self._scoreboard.set_team_value(
                team, team.score, self._score_to_win
            )

    def end_game(self) -> None:
        results = bs.GameResults()
        for team in self.teams:
            results.set_team_score(team, team.score)
        self.end(results=results)
