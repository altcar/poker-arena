# NOTE: DO NOT CHANGE THIS CODE

from typing import Annotated

from pydantic import (
    ConfigDict,
    Field,
    NonNegativeInt,
    SkipValidation,
    validate_call,
)

from card_utils import Card
from game_state import GameStateView, InvalidAction
from model import Model
from player_action import PlayerAction


class GameInterface:
    """
    Runs your model. The organiser's copy of this file is what plays.

    The engine keeps game_state up to date; this only reads it.
    """

    # NOTE: DO NOT CHANGE THIS CODE
    # The model is not checked: any object with seed() and run()
    # will do, it need not subclass Model.
    @validate_call(
        config=ConfigDict(strict=True, arbitrary_types_allowed=True)
    )
    def __init__(
        self,
        model: SkipValidation[Model],
        game_state: GameStateView,
        player_id: NonNegativeInt,
        hand: Annotated[list[Card], Field(min_length=2, max_length=2)],
    ):
        self.model = model
        self.game_state = game_state
        self.player_id = player_id  # checked against every action returned
        self.model.seed(list(hand), player_id)

    # Gateway function that runs validation
    def process_action(self, actions: list[PlayerAction]) -> PlayerAction:
        """Takes the actions since your last turn and returns your move."""
        action = self.model.run(actions, self.game_state)
        # Type, then id, then the rules: each check relies on the one before.
        # A plain isinstance, not Pydantic: its message names run().
        if not isinstance(action, PlayerAction):
            raise InvalidAction(
                "run() must return a PlayerAction, not "
                f"{type(action).__name__} ({action!r})"
            )
        if action.player_id != self.player_id:
            raise InvalidAction(
                f"your action has player_id {action.player_id}, "
                f"but you are player {self.player_id}"
            )
        self.game_state.validate_action(action)
        return action

    def end_game(self, actions: list[PlayerAction]):
        """Tells the model the hand is over: the actions since its last
        turn, shown cards and pot awards included.

        Calls model.end_game(actions, game_state) if the model has one;
        any other model is not told.
        """
        end_game = getattr(self.model, "end_game", None)
        if callable(end_game):
            end_game(actions, self.game_state)
