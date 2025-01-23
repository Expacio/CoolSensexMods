# Released under the MIT License. See LICENSE for details.
#
# pylint: disable=too-many-lines
"""Implements Squid games (both co-op and teams varieties)."""

# ba_meta require api 8
# (see https://ballistica.net/wiki/meta-tag-system)

from __future__ import annotations

import math
import random
import logging
from typing import TYPE_CHECKING, override

import bascenev1 as bs

from bascenev1lib.actor.bomb import TNTSpawner
from bascenev1lib.actor.playerspaz import PlayerSpaz
from bascenev1lib.actor.scoreboard import Scoreboard
from bascenev1lib.gameutils import SharedObjects
from bascenev1lib.actor.respawnicon import RespawnIcon
from bascenev1lib.actor.powerupbox import PowerupBoxFactory, PowerupBox
from bascenev1lib.actor.flag import (
    FlagFactory,
    Flag,
    FlagPickedUpMessage,
    FlagDroppedMessage,
    FlagDiedMessage,
)
import bascenev1lib
from bascenev1lib.actor.spazbot import (
    SpazBotDiedMessage,
    SpazBotPunchedMessage,
    SpazBotSet,
    BrawlerBotLite,
    BrawlerBot,
    BomberBotLite,
    BomberBot,
    TriggerBot,
    ChargerBot,
    TriggerBotPro,
    BrawlerBotPro,
    StickyBot,
    ExplodeyBot,
    SpazBot
)


if TYPE_CHECKING:
    from typing import Any, Sequence

    from bascenev1lib.actor.spaz import Spaz
    from bascenev1lib.actor.spazbot import SpazBot



class FootballFlag(Flag):
    """Custom flag class for football games."""

    def __init__(self, position: Sequence[float]):
        super().__init__(
            position=position, dropped_timeout=20, color=(1.0, 1.0, 0.3)
        )
        assert self.node
        self.last_holding_player: bs.Player | None = None
        self.node.is_area_of_interest = True
        self.respawn_timer: bs.Timer | None = None
        self.scored = False
        self.held_count = 0
        self.light = bs.newnode(
            'light',
            owner=self.node,
            attrs={
                'intensity': 0.25,
                'height_attenuated': False,
                'radius': 0.2,
                'color': (0.9, 0.7, 0.0),
            },
        )
        self.node.connectattr('position', self.light, 'position')


class Player(bs.Player['Team']):
    """Our player type for this game."""

    def __init__(self) -> None:
        self.respawn_timer: bs.Timer | None = None
        self.respawn_icon: RespawnIcon | None = None


class Team(bs.Team[Player]):
    """Our team type for this game."""

    def __init__(self) -> None:
        self.score = 0


# ba_meta export bascenev1.GameActivity
class RaceGame(bs.TeamGameActivity[Player, Team]):
    """Football game for teams mode."""

    name = 'Squid Game'
    description = 'Avoid getting caught by Pixie.'
    available_settings = [
        bs.IntSetting(
            'Score to Win',
            min_value=7,
            default=21,
            increment=7,
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

    @override
    @classmethod
    def supports_session_type(cls, sessiontype: type[bs.Session]) -> bool:
        # We only support two-team play.
        return issubclass(sessiontype, bs.DualTeamSession)

    @override
    @classmethod
    def get_supported_maps(cls, sessiontype: type[bs.Session]) -> list[str]:
        assert bs.app.classic is not None
        return bs.app.classic.getmaps('football')

    def __init__(self, settings: dict):
        super().__init__(settings)
        self._scoreboard: Scoreboard | None = Scoreboard()

        # Load some media we need.
        self._cheer_sound = bs.getsound('cheer')
        self._chant_sound = bs.getsound('crowdChant')
        self._score_sound = bs.getsound('score')
        shared = SharedObjects.get()
        self._swipsound = bs.getsound('swip')
        self._whistle_sound = bs.getsound('refWhistle')
        self._score_region_material = bs.Material()
        self._score_region_material.add_actions(
            conditions=('they_have_material', shared.player_material),
            actions=(
                ('modify_part_collision', 'collide', True),
                ('modify_part_collision', 'physical', False),
                ('call', 'at_connect', self._handle_score),
            ),
        )
        self._bots = SpazBotSet()
        self._flag_spawn_pos: Sequence[float] | None = None
        self.caught = []
        self._score_regions: list[bs.NodeActor] = []
        self._flag: FootballFlag | None = None
        self._flag_respawn_timer: bs.Timer | None = None
        self._flag_respawn_light: bs.NodeActor | None = None
        self._score_to_win = int(settings['Score to Win'])
        self._time_limit = float(settings['Time Limit'])
        self._epic_mode = bool(settings['Epic Mode'])
        self.slow_motion = self._epic_mode
        self.default_music = (
            bs.MusicType.EPIC if self._epic_mode else bs.MusicType.FOOTBALL
        )

    @override
    def get_instance_description(self) -> str | Sequence:
        touchdowns = self._score_to_win / 7

        # NOTE: if use just touchdowns = self._score_to_win // 7
        # and we will need to score, for example, 27 points,
        # we will be required to score 3 (not 4) goals ..
        touchdowns = math.ceil(touchdowns)
        if touchdowns > 1:
            return 'Score ${ARG1} points.', touchdowns
        return 'Score a touchdown.'

    @override
    def get_instance_description_short(self) -> str | Sequence:
        touchdowns = self._score_to_win / 7
        touchdowns = math.ceil(touchdowns)
        if touchdowns > 1:
            return 'score ${ARG1} points', touchdowns
        return 'score a point'

    def _spawn_bot(
        self, spaz_type: type[SpazBot], immediate: bool = False
    ) -> None:
        assert self._bot_team is not None
        pos = self.map.get_start_position(self._bot_team.id)
        self._bots.spawn_bot(
            spaz_type,
            pos=pos,
            spawn_time=0.001 if immediate else 3.0,
            on_spawn_call=self._on_bot_spawn,
        )

    @override
    def on_begin(self) -> None:
        super().on_begin()
        self.setup_standard_time_limit(self._time_limit)
        self.setup_standard_powerup_drops()
        defs = self.map.defs
        pos = (-0.010719207115471363, 0.3001460134983063, -5.279834747314453)
        self.spaz = bascenev1lib.actor.spaz.Spaz(character="Pixel", start_invincible=False,color=(2,0,2)).autoretain()
        self.spaz.node.handlemessage(bs.StandMessage(position=pos))
        self._score_regions.append(
            bs.NodeActor(
                bs.newnode(
                    'region',
                    attrs={
                        'position': defs.boxes['goal1'][0:3],
                        'scale': defs.boxes['goal1'][6:9],
                        'type': 'box',
                        'materials': (self._score_region_material,),
                    },
                )
            )
        )
        self._update_scoreboard()
        self._chant_sound.play()
        self.spaz.view = "wall"
        self.spaz.node.move_up_down = 2
        self.spaz.node.invincible = True
        def stop():
            self.spaz.node.move_up_down = 0
        bs.timer(0.5, stop, False)
        self._start_lights = []
        self.animt = bascenev1lib.actor.text.Text(
                "Reach the other end of this map without getting seen\nby Pixie",
                v_attach=bascenev1lib.actor.text.Text.VAttach.BOTTOM,
                h_align=bascenev1lib.actor.text.Text.HAlign.CENTER,
                position=(0, 90) if pos != "top" else (0, 650),
                shadow=0.6,
                color=(1, 1, 1),
                scale=0.8,
            ).autoretain()
        prefixAnim = {
            0: 1, 5: 0
            }
        for xpl in self.players:
            xpl.actor.connect_controls_to_player(enable_punch=False, enable_bomb=False, enable_run=False)
        bs.animate(self.animt.node, "opacity", prefixAnim, False)
        if self.slow_motion:
            t_scale = 0.4
            light_y = 50
        else:
            t_scale = 1.0
            light_y = 150
        for i in range(1):
            self.lnub = bs.newnode(
                'image',
                attrs={
                    'texture': bs.gettexture('nub'),
                    'opacity': 1.0,
                    'color': (0,1,0),
                    'absolute_scale': True,
                    'position': (20 + i * 50, light_y),
                    'scale': (50, 50),
                    'attach': 'center',
                },
            )
            bs.animate(
                self.lnub,
                'opacity',
                {
                    4.0 * t_scale: 0,
                    5.0 * t_scale: 1.0,
                    12.0 * t_scale: 1.0
                },
            )
            self._start_lights.append(self.lnub)

        def turn_light_green_and_face_pixie_opposite_to_players():
            self.spaz.node.move_up_down = 2
            self.spaz.view = "wall"
            bs.timer(0.2, stop)
            turn_light_green()
        def turn_light_green():
            bs.getsound('raceBeep2').play()
            self._start_lights[0].color = (0,1,0)
        def turn_light_red():
            bs.getsound('error').play()
            self._start_lights[0].color = (1,0,0)
        def turn_light_red_and_face_pixie_towards_players():
            self.spaz.node.move_up_down = -2
            self.spaz.view = "players"
            bs.timer(0.2, stop)
            turn_light_red()
        def loop():
            turn_light_green()
            bs.timer(0.5, turn_light_red_and_face_pixie_towards_players)
            bs.timer(random.uniform(3,5), turn_light_green_and_face_pixie_opposite_to_players)
            
        bs.timer(6, loop, True)
        bs.timer(0.1, self.kill_moving_players, True)
        
            
        
    def kill_moving_players(self):
        for xpl in self.players:
            xpl.actor.connect_controls_to_player(enable_punch=False, enable_bomb=False, enable_run=False)
        if self.spaz.view == "players":
            for i in self.players:
                if i.actor.is_alive() and hasattr(i.actor.node, 'move_up_down') and hasattr(i.actor.node, 'move_left_right') and not i in self.caught:
                    if i.actor.node.move_up_down != 0 or i.actor.node.move_left_right != 0:
                        bs.screenmessage(f"{i.getname()} was caught!", color=(1,1,0))
                        def update(): #The following function and the loop are for showing a kill effect. 
                            try:
                                pos = i.actor.node.position
                                i.actor.node.handlemessage("impulse", pos[0],
                                                        pos[1] + 4, pos[2], 0, 5, 0,
                                                        3, 10, 0, 0, 0, 5, 0)
                                self.caught.append(i)
                                def kl():
                                    i.actor.node.handlemessage(bs.DieMessage(immediate=True))
                                bs.timer(1,kl)
                            except Exception as e:
                                pass    
                        

                        delay = 0
                        for x in range(40):
                            bs.timer(delay, bs.Call(update))
                            delay += 0.025

    @override
    def on_team_join(self, team: Team) -> None:
        self._update_scoreboard()


    def _handle_score(self) -> None:
        """A point has been scored."""
        try:
            spaz = self.spaz
            if spaz.node == bs.getcollision().opposingnode:
                return
        except bs.NotFoundError:
            pass
        opposingnode = bs.getcollision().opposingnode
        opposingnode.source_player.team.score += 1
        opposingnode.handlemessage(bs.StandMessage(position=self.map.get_start_position(self.teams[0].id)))
        opposingnode.handlemessage('celebrate', 2000)
        self._score_sound.play()
        self._cheer_sound.play()
        self._chant_sound.play()
        self._update_scoreboard()

    @override
    def end_game(self) -> None:
        results = bs.GameResults()
        for team in self.teams:
            results.set_team_score(team, team.score)
        self.end(results=results, announce_delay=0.8)

    def _update_scoreboard(self) -> None:
        assert self._scoreboard is not None
        for team in self.teams:
            self._scoreboard.set_team_value(
                team, team.score, self._score_to_win
            )

    @override
    def spawn_player(self, player):
        self.spawn_player_spaz(player, position=self.map.get_start_position(self.teams[0].id))

    @override
    def handlemessage(self, msg: Any) -> Any:

        # Respawn dead players if they're still in the game.
        if isinstance(msg, bs.PlayerDiedMessage):
            # Augment standard behavior.
            super().handlemessage(msg)
            self.respawn_player(msg.getplayer(Player))

        # Respawn dead flags.

        else:
            # Augment standard behavior.
            super().handlemessage(msg)

